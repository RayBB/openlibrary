mod helpers;
mod query;
mod parquet;
mod transform;

use clap::Parser;
use anyhow::Result;
use rayon::prelude::*;

#[derive(Parser, Debug)]
struct Args {
    #[arg(long, default_value = "10")]
    limit: usize,
    #[arg(long, default_value = "/works/OL1W")]
    start_at: String,
    #[arg(long, default_value = "lake/bronze/works.parquet")]
    bronze_works: String,
    #[arg(long, default_value = "lake/silver/editions.parquet")]
    silver_editions: String,
    #[arg(long, default_value = "lake/bronze/authors.parquet")]
    bronze_authors: String,
    #[arg(long, default_value = "lake/gold/rust_sample.parquet")]
    out: String,
    #[arg(long, default_value = "18")]
    workers: usize,
}

fn main() -> Result<()> {
    let args = Args::parse();
    // set rayon threads
    rayon::ThreadPoolBuilder::new().num_threads(args.workers).build_global().ok();

    let t0 = std::time::Instant::now();
    let fetched = query::fetch(args.limit, &args.start_at, &args.bronze_works, &args.silver_editions, &args.bronze_authors)?;
    let prep = t0.elapsed().as_secs_f64();
    eprintln!("Fetched {} works", fetched.works.len());

    // chunk + parallel transform (filtered per chunk like DC)
    let chunk_size = (fetched.works.len() + args.workers - 1) / args.workers.max(1);
    let chunks: Vec<Vec<serde_json::Value>> = fetched.works.chunks(chunk_size).map(|c| c.to_vec()).collect();

    let t1 = std::time::Instant::now();
    let docs_per_chunk: Vec<Vec<serde_json::Value>> = chunks
        .par_iter()
        .map(|chunk| {
            // filtered editions/authors per chunk
            let ck: std::collections::HashSet<String> = chunk.iter().filter_map(|w| w.get("key").and_then(|k| k.as_str()).map(|s| s.to_string())).collect();
            let mut ce: std::collections::HashMap<String, Vec<serde_json::Value>> = std::collections::HashMap::new();
            for k in &ck {
                if let Some(v) = fetched.editions_by_work.get(k) {
                    ce.insert(k.clone(), v.clone());
                }
            }
            let mut cak = std::collections::HashSet::new();
            for w in chunk {
                if let Some(arr) = w.get("authors").and_then(|v| v.as_array()) {
                    for a in arr {
                        let ak = a.get("author").and_then(|v| {
                            if v.is_object() { v.get("key").and_then(|k| k.as_str()).map(|s| s.to_string()) }
                            else if v.is_string() { v.as_str().map(|s| s.to_string()) }
                            else { None }
                        }).or_else(|| a.get("key").and_then(|v| v.as_str()).map(|s| s.to_string()));
                        if let Some(k) = ak { cak.insert(k); }
                    }
                }
            }
            let mut ca: Vec<serde_json::Value> = Vec::new();
            for k in cak {
                if let Some(v) = fetched.authors.get(&k) {
                    ca.push(v.clone());
                }
            }
            // build docs
            let mut docs = Vec::new();
            for w in chunk {
                let eds = ce.get(w.get("key").and_then(|v| v.as_str()).unwrap_or("")).map(|v| v.as_slice()).unwrap_or(&[]);
                // filter authors for this work
                let work_author_keys: std::collections::HashSet<String> = w.get("authors").and_then(|v| v.as_array()).map(|arr| {
                    arr.iter().filter_map(|a| {
                        a.get("author").and_then(|v| {
                            if v.is_object() { v.get("key").and_then(|k| k.as_str()).map(|s| s.to_string()) }
                            else if v.is_string() { v.as_str().map(|s| s.to_string()) }
                            else { None }
                        }).or_else(|| a.get("key").and_then(|v| v.as_str()).map(|s| s.to_string()))
                    }).collect()
                }).unwrap_or_default();
                let work_authors: Vec<serde_json::Value> = ca.iter().filter(|a| {
                    a.get("key").and_then(|k| k.as_str()).map(|k| work_author_keys.contains(k)).unwrap_or(false)
                }).cloned().collect();
                // also need authors for alternative that may be missing? use work_authors
                let doc = transform::build_solr_doc(w, eds, &work_authors);
                docs.push(doc);
            }
            docs
        })
        .collect();

    let docs: Vec<serde_json::Value> = docs_per_chunk.into_iter().flatten().collect();
    let build = t1.elapsed().as_secs_f64();
    eprintln!("Transform {} docs build {:.2}s {:.1} docs/s", docs.len(), build, docs.len() as f64 / build.max(0.001));

    let t2 = std::time::Instant::now();
    parquet::write_gold(&docs, &args.out)?;
    let write_t = t2.elapsed().as_secs_f64();
    let total = t0.elapsed().as_secs_f64();
    eprintln!("Wrote {} to {} in {:.2}s total {:.2}s (prep {:.2}s + build {:.2}s)", docs.len(), args.out, write_t, total, prep, build);
    // estimate
    let full = 14406749.0;
    let est_build = (full / args.limit as f64) * build;
    let est_total = (full / args.limit as f64) * total;
    eprintln!("Estimate full 14.4M: build {:.2}h total {:.2}h", est_build/3600.0, est_total/3600.0);
    Ok(())
}
