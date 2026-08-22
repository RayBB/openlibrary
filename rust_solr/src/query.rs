use anyhow::Result;
use duckdb::{Connection, params};
use serde_json::Value;

pub struct Fetched {
    pub works: Vec<Value>,
    pub work_keys: Vec<String>,
    pub editions_by_work: std::collections::HashMap<String, Vec<Value>>,
    pub authors: std::collections::HashMap<String, Value>,
}

pub fn fetch(limit: usize, start_at: &str, bronze_works: &str, silver_editions: &str, bronze_authors: &str) -> Result<Fetched> {
    let con = Connection::open_in_memory()?;
    let t0 = std::time::Instant::now();
    con.execute(&format!("CREATE TEMP TABLE sample_keys AS SELECT Key, JSON FROM '{}' WHERE Key >= '{}' ORDER BY Key LIMIT {}", bronze_works, start_at, limit), [])?;
    let mut stmt = con.prepare("SELECT Key, JSON FROM sample_keys ORDER BY Key")?;
    let rows = stmt.query_map([], |row| {
        let k: String = row.get(0)?;
        let j: String = row.get(1)?;
        Ok((k,j))
    })?.collect::<Result<Vec<_>, _>>()?;
    let t1 = std::time::Instant::now();
    // editions
    let mut stmt2 = con.prepare(&format!("SELECT e.work_key, e.JSON FROM '{}' e JOIN sample_keys s ON e.work_key = s.Key", silver_editions))?;
    let edition_rows = stmt2.query_map([], |row| {
        let wkey: Option<String> = row.get(0)?;
        let j: String = row.get(1)?;
        Ok((wkey, j))
    })?.collect::<Result<Vec<_>, _>>()?;
    let t2 = std::time::Instant::now();

    let mut works = Vec::new();
    let mut work_keys = Vec::new();
    for (k,j) in rows {
        let v: Value = serde_json::from_str(&j).unwrap_or_else(|_| serde_json::from_str(&j.replace("'", "\"")).unwrap_or(json!({})));
        works.push(v);
        work_keys.push(k);
    }
    let work_keys_set: std::collections::HashSet<String> = work_keys.iter().cloned().collect();
    let mut editions_by_work: std::collections::HashMap<String, Vec<Value>> = work_keys.iter().map(|k| (k.clone(), Vec::new())).collect();
    for (wkey_opt, j) in edition_rows {
        if let Some(wkey) = wkey_opt {
            if work_keys_set.contains(&wkey) {
                let v: Value = serde_json::from_str(&j).unwrap_or(json!({}));
                if let Some(entry) = editions_by_work.get_mut(&wkey) {
                    entry.push(v);
                }
            }
        }
    }
    // author keys
    let mut author_keys = std::collections::HashSet::new();
    for w in &works {
        if let Some(arr) = w.get("authors").and_then(|v| v.as_array()) {
            for a in arr {
                let ak = a.get("author").and_then(|v| {
                    if v.is_object() { v.get("key").and_then(|k| k.as_str()).map(|s| s.to_string()) }
                    else if v.is_string() { v.as_str().map(|s| s.to_string()) }
                    else { None }
                }).or_else(|| a.get("key").and_then(|v| v.as_str()).map(|s| s.to_string()));
                if let Some(k) = ak { author_keys.insert(k); }
            }
        }
    }
    // fetch authors via duckdb ANY(?) - duckdb-rs may not support list param, fallback to temp table
    let author_keys_vec: Vec<String> = author_keys.into_iter().collect();
    let con2 = Connection::open_in_memory()?;
    let mut authors = std::collections::HashMap::new();
    if !author_keys_vec.is_empty() {
        // Direct temp table approach (duckdb-rs doesn't support Vec<String> As ToSql for ANY)
        con2.execute("CREATE TEMP TABLE ak (Key VARCHAR)", [])?;
        // bulk insert via appender for speed
        {
            let mut app = con2.appender("ak")?;
            for k in &author_keys_vec {
                app.append_row(params![k])?;
            }
            app.flush()?;
        }
        let mut stmt4 = con2.prepare(&format!("SELECT a.JSON FROM '{}' a JOIN ak ON a.Key = ak.Key", bronze_authors))?;
        let iter = stmt4.query_map([], |row| { let j: String = row.get(0)?; Ok(j) })?;
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
    eprintln!("Prepare: works {:.2}s editions {:.2}s authors {:.2}s total {:.2}s", (t1-t0).as_secs_f64(), (t2-t1).as_secs_f64(), (t3-t2).as_secs_f64(), (t3-t0).as_secs_f64());
    Ok(Fetched { works, work_keys, editions_by_work, authors })
}

use serde_json::json;
