use anyhow::Result;
use duckdb::{Connection, params};
use serde_json::Value;

pub struct Fetched {
    pub works: Vec<Value>,
    pub work_keys: Vec<String>,
    pub editions_by_work: std::collections::HashMap<String, Vec<Value>>,
    pub authors: std::collections::HashMap<String, Value>,
}

#[derive(serde::Deserialize, Clone, Debug)]
pub struct Chunk {
    pub n: usize,
    pub lo: i64,
    pub hi: i64,
}

fn bucket_paths(base: &std::path::Path, bmin: i64, bmax: i64) -> Vec<String> {
    let mut paths = Vec::new();
    for b in bmin..=bmax {
        let p = base.join(format!("bucket={}/data.parquet", b));
        if p.exists() {
            paths.push(p.to_string_lossy().to_string());
        }
    }
    paths
}

fn read_bucket_rows(
    con: &Connection,
    paths: &[String],
    id_filter: Option<(i64, i64)>,
    cols: &str,
) -> Result<Vec<(Option<String>, String)>> {
    if paths.is_empty() {
        return Ok(Vec::new());
    }
    let list = paths.iter().map(|p| format!("'{}'", p)).collect::<Vec<_>>().join(",");
    let sql = match id_filter {
        Some((lo, hi)) => format!(
            "SELECT {} FROM read_parquet([{}]) WHERE id BETWEEN {} AND {}",
            cols, list, lo, hi
        ),
        None => format!("SELECT {} FROM read_parquet([{}])", cols, list),
    };
    let mut stmt = con.prepare(&sql)?;
    let rows = stmt
        .query_map([], |row| {
            let a: Option<String> = row.get(0)?;
            let j: String = row.get(1)?;
            Ok((a, j))
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

pub fn fetch(
    limit: usize,
    offset: usize,
    start_at: &str,
    bronze_works: &str,
    silver_editions: &str,
    bronze_authors: &str,
    chunks_path: Option<&str>,
    chunk_index: usize,
) -> Result<Fetched> {
    let mut con = Connection::open_in_memory()?;
    let t0 = std::time::Instant::now();

    let chunk: Option<Chunk> = chunks_path.and_then(|cp| {
        let data = std::fs::read_to_string(cp).ok()?;
        let chunks: Vec<Chunk> = serde_json::from_str(&data).ok()?;
        chunks.get(chunk_index).cloned()
    });

    // Chunked mode: numeric-OLID ranges; works/editions both bucketed by id//100000,
    // zonemap-prunable reads.
    let (works_raw, edition_raw): (Vec<(String, String)>, Vec<(Option<String>, String)>);
    let mut is_chunked = false;
    if let Some(c) = &chunk {
        let silver_parent = std::path::Path::new(silver_editions)
            .parent()
            .unwrap_or(std::path::Path::new("lake/silver"));
        let works_base = silver_parent.join("works_b");
        let ed_base = silver_parent.join("editions_bucketed");
        let (lo, hi) = (c.lo, c.hi);
        is_chunked = true;

        let wpaths = bucket_paths(&works_base, lo / 100000, hi / 100000);
        works_raw = read_bucket_rows(&con, &wpaths, Some((lo, hi)), "Key, JSON")?
            .into_iter()
            .map(|(k, j)| (k.unwrap_or_default(), j))
            .collect();

        let epaths = bucket_paths(&ed_base, lo / 100000, hi / 100000);
        edition_raw = read_bucket_rows(&con, &epaths, None, "work_key, JSON")?;
    } else {
        // Legacy path: lexicographic sample window; requires ORDER BY for determinism.
        let _ = offset; // unused, start_at drives pagination when not chunked
        let cmp = if start_at == "/works/OL1W" && offset == 0 {
            ">="
        } else {
            ">"
        };
        con.execute(&format!(
            "CREATE TEMP TABLE sk AS SELECT Key FROM '{}' WHERE Key {} '{}' ORDER BY Key LIMIT {}",
            bronze_works, cmp, start_at, limit
        ), [])?;
        works_raw = {
            let mut stmt = con.prepare(&format!(
                "SELECT w.Key, w.JSON FROM '{}' w JOIN sk ON w.Key = sk.Key ORDER BY w.Key",
                bronze_works
            ))?;
            stmt.query_map([], |row| {
                let k: String = row.get(0)?;
                let j: String = row.get(1)?;
                Ok((k, j))
            })?
            .collect::<Result<Vec<_>, _>>()?
        };
        let mut stmt2 = con.prepare(&format!(
            "SELECT e.work_key, e.JSON FROM '{}' e JOIN sk ON e.work_key = sk.Key",
            silver_editions
        ))?;
        edition_raw = stmt2
            .query_map([], |row| {
                let wkey: Option<String> = row.get(0)?;
                let j: String = row.get(1)?;
                Ok((wkey, j))
            })?
            .collect::<Result<Vec<_>, _>>()?;
    }
    let t1 = std::time::Instant::now();

    // Parse works
    let mut works = Vec::with_capacity(works_raw.len());
    let mut work_keys = Vec::with_capacity(works_raw.len());
    for (k, j) in works_raw {
        let v: Value = serde_json::from_str(&j)
            .unwrap_or_else(|_| serde_json::from_str(&j.replace("'", "\"")).unwrap_or(json!({})));
        works.push(v);
        work_keys.push(k);
    }

    let work_keys_set: std::collections::HashSet<String> = work_keys.iter().cloned().collect();
    let mut editions_by_work: std::collections::HashMap<String, Vec<Value>> =
        work_keys.iter().map(|k| (k.clone(), Vec::new())).collect();
    for (wkey_opt, j) in edition_raw {
        if let Some(wkey) = wkey_opt {
            if let Some(entry) = editions_by_work.get_mut(&wkey) {
                let v: Value = serde_json::from_str(&j).unwrap_or(json!({}));
                entry.push(v);
            } else if is_chunked && work_keys_set.contains(&wkey) {
                let v: Value = serde_json::from_str(&j).unwrap_or(json!({}));
                editions_by_work.entry(wkey).or_default().push(v);
            }
        }
    }
    let t2 = std::time::Instant::now();

    // Authors: collect keys in Rust then appender JOIN (keep for now)
    let mut author_keys = std::collections::HashSet::new();
    for w in &works {
        if let Some(arr) = w.get("authors").and_then(|v| v.as_array()) {
            for a in arr {
                let ak = a
                    .get("author")
                    .and_then(|v| {
                        if v.is_object() {
                            v.get("key").and_then(|k| k.as_str()).map(|s| s.to_string())
                        } else if v.is_string() {
                            v.as_str().map(|s| s.to_string())
                        } else {
                            None
                        }
                    })
                    .or_else(|| a.get("key").and_then(|v| v.as_str()).map(|s| s.to_string()));
                if let Some(k) = ak {
                    author_keys.insert(k);
                }
            }
        }
    }
    let author_keys_vec: Vec<String> = author_keys.into_iter().collect();
    let mut authors = std::collections::HashMap::new();
    if !author_keys_vec.is_empty() {
        if is_chunked {
            // chunked: new connection pattern same; reuse con is fine
            con.execute("CREATE TEMP TABLE ak (Key VARCHAR)", [])?;
        } else {
            con.execute("CREATE TEMP TABLE ak (Key VARCHAR)", [])?;
        }
        {
            let mut app = con.appender("ak")?;
            for k in &author_keys_vec {
                app.append_row(params![k])?;
            }
            app.flush()?;
        }
        let mut stmt4 = con.prepare(&format!(
            "SELECT a.JSON FROM '{}' a JOIN ak ON a.Key = ak.Key",
            bronze_authors
        ))?;
        let iter = stmt4.query_map([], |row| {
            let j: String = row.get(0)?;
            Ok(j)
        })?;
        let author_jsons: Vec<String> = iter.collect::<Result<Vec<String>, _>>()?;
        for j in author_jsons {
            if let Ok(v) = serde_json::from_str::<Value>(&j) {
                if let Some(k) = v.get("key").and_then(|x| x.as_str()) {
                    authors.insert(k.to_string(), v);
                }
            }
        }
    }
    let t3 = std::time::Instant::now();
    eprintln!(
        "Prepare: works {:.2}s editions {:.2}s authors {:.2}s total {:.2}s",
        (t1 - t0).as_secs_f64(),
        (t2 - t1).as_secs_f64(),
        (t3 - t2).as_secs_f64(),
        (t3 - t0).as_secs_f64()
    );
    Ok(Fetched {
        works,
        work_keys,
        editions_by_work,
        authors,
    })
}

use serde_json::json;
