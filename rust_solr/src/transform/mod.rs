use std::collections::{HashMap, HashSet};

use serde_json::{json, Value};

use crate::helpers::{
    ddc::{choose_sorting_ddc, normalize_ddc},
    isbn::opposite_isbn,
    lcc::{choose_sorting_lcc, short_lcc_to_sortable_lcc},
    sort_title::sort_title,
};
use once_cell::sync::Lazy;

static RE_YEAR: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"\b(\d{4})\b").unwrap());
static RE_LANG_KEY: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"^/(?:l|languages)/([a-z]{3})$").unwrap());
static RE_AUTHOR_KEY: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"^/(?:a|authors)/(OL\d+A)").unwrap());
static RE_SOLR_FIELD: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"^[-\w]+$").unwrap());
static RE_DDC_SMALL: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"^0?\d{1,2}$").unwrap());
static RE_SOLR_ESCAPE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r#"([\s\-+!()|&{}\[\]^"~*?:\\])"#).unwrap());

fn get_str(v: &Value, key: &str) -> Option<String> {
    v.get(key).and_then(|x| x.as_str()).map(|s| s.to_string())
}

fn get_array_str(v: &Value, key: &str) -> Vec<String> {
    v.get(key)
        .and_then(|x| x.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|e| e.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

fn uniq_strings(vals: Vec<String>) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for v in vals {
        if seen.insert(v.clone()) {
            out.push(v);
        }
    }
    out
}

fn extract_year(publish_date: &str) -> Option<i64> {
    RE_YEAR
        .captures(publish_date)
        .and_then(|c| c.get(1))
        .and_then(|m| m.as_str().parse().ok())
}

fn solr_escape(query: &str) -> String {
    RE_SOLR_ESCAPE.replace_all(query, r"\$1").to_string()
}

// Edition builder helpers
fn edition_isbn(ed: &Value) -> Vec<String> {
    let mut isbns = Vec::new();
    for k in &["isbn_13", "isbn_10"] {
        if let Some(arr) = ed.get(*k).and_then(|v| v.as_array()) {
            for v in arr {
                if let Some(s) = v.as_str() {
                    let t = s.replace('_', "").trim().to_string();
                    if !t.is_empty() {
                        isbns.push(t);
                    }
                }
            }
        }
    }
    let mut extra = Vec::new();
    for isbn in &isbns {
        if let Some(o) = opposite_isbn(isbn) {
            extra.push(o);
        }
    }
    isbns.extend(extra);
    uniq_strings(isbns.into_iter().filter(|s| !s.is_empty()).collect())
}

fn edition_publish_year(ed: &Value) -> Option<i64> {
    ed.get("publish_date")
        .and_then(|v| v.as_str())
        .and_then(extract_year)
}

fn edition_publishers(ed: &Value) -> Vec<String> {
    let pubs = get_array_str(ed, "publishers");
    pubs.into_iter()
        .map(|p| {
            let trimmed: String = p.chars().filter(|c| c.is_alphabetic()).collect();
            if trimmed.to_lowercase() == "sn" {
                "Sine nomine".to_string()
            } else {
                p
            }
        })
        .collect()
}

fn edition_languages(ed: &Value) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(arr) = ed.get("languages").and_then(|v| v.as_array()) {
        for lang in arr {
            let key = if let Some(s) = lang.as_str() {
                s.to_string()
            } else if let Some(o) = lang.as_object() {
                o.get("key").and_then(|k| k.as_str()).unwrap_or("").to_string()
            } else {
                String::new()
            };
            if let Some(caps) = RE_LANG_KEY.captures(&key) {
                out.push(caps.get(1).unwrap().as_str().to_string());
            }
        }
    }
    uniq_strings(out)
}

pub fn build_solr_doc(
    work: &Value,
    editions: &[Value],
    authors: &[Value],
) -> Value {
    let key = get_str(work, "key").unwrap_or_default();
    let title = work.get("title").and_then(|v| v.as_str()).map(|s| s.to_string()).or_else(|| {
        // fallback to first edition title
        editions.iter().find_map(|ed| ed.get("title").and_then(|v| v.as_str()).map(|s| s.to_string())).or(Some("__None__".to_string()))
    });
    let subtitle = work.get("subtitle").and_then(|v| v.as_str()).map(|s| s.to_string());
    let title_sort = title.as_ref().map(|t| sort_title(t, subtitle.as_deref()));

    // edition_count
    let edition_count = editions.len() as i64;
    let edition_key: Vec<String> = editions.iter().filter_map(|ed| ed.get("key").and_then(|k| k.as_str()).map(|s| s.split('/').last().unwrap_or(s).to_string())).collect();

    // author fields
    let author_key: Vec<String> = authors.iter().filter_map(|a| {
        let k = a.get("key")?.as_str()?;
        RE_AUTHOR_KEY
            .captures(k)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().to_string())
    }).collect();
    let author_name: Vec<String> = authors.iter().map(|a| a.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string()).collect();
    let author_facet: Vec<String> = author_key.iter().zip(author_name.iter()).map(|(k,n)| format!("{} {}", k, n)).collect();
    let author_alt: HashSet<String> = authors.iter().flat_map(|a| {
        a.get("alternate_names").and_then(|v| v.as_array()).map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect::<Vec<_>>()).unwrap_or_default()
    }).collect();

    // publishers, publish_year, etc aggregated from editions
    let publishers: HashSet<String> = editions.iter().flat_map(|ed| edition_publishers(ed)).collect();
    let publish_year: HashSet<i64> = editions.iter().filter_map(|ed| edition_publish_year(ed)).collect();
    let first_publish_year = publish_year.iter().cloned().min();
    let publish_date: HashSet<String> = editions.iter().filter_map(|ed| ed.get("publish_date").and_then(|v| v.as_str()).map(|s| s.to_string())).collect();
    let language: HashSet<String> = editions.iter().flat_map(|ed| edition_languages(ed)).collect();
    let lccn: HashSet<String> = editions.iter().flat_map(|ed| get_array_str(ed, "lccn").into_iter().map(|s| s.trim().to_string()).filter(|s| !s.is_empty())).collect();
    let oclc: HashSet<String> = editions.iter().flat_map(|ed| get_array_str(ed, "oclc_numbers").into_iter().map(|s| s.trim().to_string()).filter(|s| !s.is_empty())).collect();
    let isbn: HashSet<String> = editions.iter().flat_map(|ed| edition_isbn(ed)).collect();
    let contributor: HashSet<String> = editions.iter().flat_map(|ed| ed.get("contributions").and_then(|v| v.as_array()).map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect::<Vec<_>>()).unwrap_or_default()).collect();
    let publish_place: HashSet<String> = editions.iter().flat_map(|ed| get_array_str(ed, "publish_places")).collect();
    let first_sentence: HashSet<String> = editions.iter().filter_map(|ed| {
        ed.get("first_sentence").map(|v| {
            if let Some(s) = v.as_str() { s.to_string() } else if let Some(o) = v.as_object() { o.get("value").and_then(|x| x.as_str()).unwrap_or("").to_string() } else { String::new() }
        }).filter(|s| !s.is_empty())
    }).collect();

    // lcc - preserve order via Vec uniq (like Python set insertion order)
    let raw_lccs_vec: Vec<String> = editions.iter().flat_map(|ed| get_array_str(ed, "lc_classifications")).collect();
    let raw_lccs_uniq = uniq_strings(raw_lccs_vec);
    let mut lcc_vec: Vec<String> = Vec::new();
    for l in raw_lccs_uniq {
        if let Some(s) = short_lcc_to_sortable_lcc(&l) {
            if !lcc_vec.contains(&s) { lcc_vec.push(s); }
        }
    }
    let lcc_sort = if !lcc_vec.is_empty() {
        Some(choose_sorting_lcc(&lcc_vec))
    } else { None };
    let lcc_set: HashSet<String> = lcc_vec.iter().cloned().collect();

    // ddc - preserve order
    let raw_ddcs: Vec<String> = editions.iter().flat_map(|ed| crate::helpers::get_edition_ddcs(ed)).collect();
    let mut ddc_vec: Vec<String> = Vec::new();
    for raw in raw_ddcs {
        for d in normalize_ddc(&raw) {
            if !ddc_vec.contains(&d) { ddc_vec.push(d); }
        }
    }
    let ddc_sort = if !ddc_vec.is_empty() {
        Some(choose_sorting_ddc(&ddc_vec))
    } else { None };
    let ddc_set: HashSet<String> = ddc_vec.iter().cloned().collect();

    // subjects
    let mut doc = serde_json::Map::new();
    doc.insert("key".to_string(), json!(key));
    doc.insert("type".to_string(), json!("work"));
    if let Some(t) = title { doc.insert("title".to_string(), json!(t)); }
    if let Some(s) = subtitle { doc.insert("subtitle".to_string(), json!(s)); }
    if let Some(ts) = title_sort { doc.insert("title_sort".to_string(), json!(ts)); }
    doc.insert("edition_count".to_string(), json!(edition_count));
    if !edition_key.is_empty() { doc.insert("edition_key".to_string(), json!(uniq_strings(edition_key))); }
    if !author_key.is_empty() { doc.insert("author_key".to_string(), json!(author_key)); }
    if !author_name.is_empty() { doc.insert("author_name".to_string(), json!(author_name)); }
    if !author_facet.is_empty() { doc.insert("author_facet".to_string(), json!(author_facet)); }
    if !author_alt.is_empty() { doc.insert("author_alternative_name".to_string(), json!(author_alt.into_iter().collect::<Vec<_>>())); }

    // alternative_title etc: use edition alternative_title logic minimal
    let mut alt_titles = HashSet::new();
    for ed in editions {
        if let Some(t) = ed.get("title").and_then(|v| v.as_str()) {
            let mut full = t.to_string();
            if let Some(sub) = ed.get("subtitle").and_then(|v| v.as_str()) {
                full = format!("{}: {}", full, sub);
            }
            alt_titles.insert(full);
        }
        for wt in get_array_str(ed, "work_titles") { alt_titles.insert(wt); }
        for ot in get_array_str(ed, "other_titles") { alt_titles.insert(ot); }
        if let Some(tr) = ed.get("translation_of").and_then(|v| v.as_str()) { alt_titles.insert(tr.to_string()); }
    }
    // also from work as solr_edition
    if let Some(t) = work.get("title").and_then(|v| v.as_str()) {
        let mut full = t.to_string();
        if let Some(sub) = work.get("subtitle").and_then(|v| v.as_str()) { full = format!("{}: {}", full, sub); }
        alt_titles.insert(full);
    }
    if !alt_titles.is_empty() { doc.insert("alternative_title".to_string(), json!(alt_titles.into_iter().collect::<Vec<_>>())); }

    let alt_sub: HashSet<String> = editions.iter().filter_map(|ed| ed.get("subtitle").and_then(|v| v.as_str()).map(|s| s.to_string())).collect();
    if !alt_sub.is_empty() { doc.insert("alternative_subtitle".to_string(), json!(alt_sub.into_iter().collect::<Vec<_>>())); }

    if !publishers.is_empty() { doc.insert("publisher".to_string(), json!(publishers.into_iter().collect::<Vec<_>>())); }
    if !publish_date.is_empty() { doc.insert("publish_date".to_string(), json!(publish_date.into_iter().collect::<Vec<_>>())); }
    if !publish_year.is_empty() { doc.insert("publish_year".to_string(), json!(publish_year.into_iter().collect::<Vec<_>>())); }
    if let Some(y) = first_publish_year { doc.insert("first_publish_year".to_string(), json!(y)); }
    if !language.is_empty() { doc.insert("language".to_string(), json!(language.into_iter().collect::<Vec<_>>())); }
    if !lccn.is_empty() { doc.insert("lccn".to_string(), json!(lccn.into_iter().collect::<Vec<_>>())); }
    if !oclc.is_empty() { doc.insert("oclc".to_string(), json!(oclc.into_iter().collect::<Vec<_>>())); }
    if !isbn.is_empty() { doc.insert("isbn".to_string(), json!(isbn.into_iter().collect::<Vec<_>>())); }
    if !contributor.is_empty() { doc.insert("contributor".to_string(), json!(contributor.into_iter().collect::<Vec<_>>())); }
    if !publish_place.is_empty() { doc.insert("publish_place".to_string(), json!(publish_place.into_iter().collect::<Vec<_>>())); }
    if !first_sentence.is_empty() { doc.insert("first_sentence".to_string(), json!(first_sentence.into_iter().collect::<Vec<_>>())); }
    if !lcc_set.is_empty() { doc.insert("lcc".to_string(), json!(lcc_set.into_iter().collect::<Vec<_>>())); }
    if let Some(s) = lcc_sort { doc.insert("lcc_sort".to_string(), json!(s)); }
    if !ddc_set.is_empty() { doc.insert("ddc".to_string(), json!(ddc_set.into_iter().collect::<Vec<_>>())); }
    if let Some(s) = ddc_sort { doc.insert("ddc_sort".to_string(), json!(s)); }

    // seed
    let mut seed_vals: Vec<String> = Vec::new();
    for ed in editions {
        if let Some(k) = ed.get("key").and_then(|v| v.as_str()) { seed_vals.push(k.to_string()); }
    }
    seed_vals.push(key.clone());
    for a in authors { if let Some(k) = a.get("key").and_then(|v| v.as_str()) { seed_vals.push(k.to_string()); } }
    for s in work.get("subjects").and_then(|v| v.as_array()).unwrap_or(&vec![]) {
        if let Some(name) = s.as_str() { seed_vals.push(crate::helpers::subject_name_to_key("subject", name)); }
    }
    for s in work.get("subject_people").and_then(|v| v.as_array()).unwrap_or(&vec![]) {
        if let Some(name) = s.as_str() { seed_vals.push(crate::helpers::subject_name_to_key("person", name)); }
    }
    for s in work.get("subject_places").and_then(|v| v.as_array()).unwrap_or(&vec![]) {
        if let Some(name) = s.as_str() { seed_vals.push(crate::helpers::subject_name_to_key("place", name)); }
    }
    for s in work.get("subject_times").and_then(|v| v.as_array()).unwrap_or(&vec![]) {
        if let Some(name) = s.as_str() { seed_vals.push(crate::helpers::subject_name_to_key("time", name)); }
    }
    let seed_uniq = uniq_strings(seed_vals);
    if !seed_uniq.is_empty() { doc.insert("seed".to_string(), json!(seed_uniq)); }
    // series (minimal, without fetching series docs, fallback to key)
    {
        let mut series_keys = Vec::new();
        let mut series_names = Vec::new();
        let mut series_positions = Vec::new();
        // collect from work and editions (work.py:105 uniq by series key)
        let mut seen_series = HashSet::new();
        let mut all_series_edges: Vec<Value> = Vec::new();
        if let Some(arr) = work.get("series").and_then(|v| v.as_array()) {
            for s in arr { if s.is_object() { all_series_edges.push(s.clone()); } }
        }
        for ed in editions {
            if let Some(arr) = ed.get("series").and_then(|v| v.as_array()) {
                for s in arr {
                    // edition series may be string or dict; only dict with series key
                    if let Some(obj) = s.as_object() {
                        if obj.contains_key("series") { all_series_edges.push(s.clone()); }
                    }
                }
            }
        }
        // uniq by series key
        let mut uniq_edges = Vec::new();
        for edge in all_series_edges {
            if let Some(key) = edge.get("series").and_then(|v| v.get("key")).and_then(|k| k.as_str()) {
                if seen_series.insert(key.to_string()) {
                    uniq_edges.push(edge);
                }
            }
        }
        for edge in uniq_edges {
            if let Some(series_key) = edge.get("series").and_then(|v| v.get("key")).and_then(|k| k.as_str()) {
                let olid = series_key.split('/').last().unwrap_or(series_key).to_string();
                series_keys.push(olid);
                // name fallback to key if not present (since we don't fetch series docs)
                let name = edge.get("series").and_then(|v| v.get("name")).and_then(|n| n.as_str()).unwrap_or(series_key);
                series_names.push(name.to_string());
                let pos = edge.get("position").and_then(|v| v.as_str()).unwrap_or("").to_string();
                series_positions.push(pos);
            }
        }
        if !series_keys.is_empty() {
            doc.insert("series_key".to_string(), json!(series_keys));
            doc.insert("series_name".to_string(), json!(series_names));
            doc.insert("series_position".to_string(), json!(series_positions));
        }
    }

    // subjects
    let field_map = [("subjects","subject"),("subject_places","place"),("subject_times","time"),("subject_people","person")];
    for (work_field, subject_type) in field_map {
        if let Some(arr) = work.get(work_field).and_then(|v| v.as_array()) {
            if arr.is_empty() { continue; }
            let vals: Vec<String> = arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect();
            if vals.is_empty() { continue; }
            doc.insert(subject_type.to_string(), json!(vals.clone()));
            doc.insert(format!("{}_facet", subject_type), json!(vals.clone()));
            let keys: Vec<String> = vals.iter().map(|s| crate::helpers::normalize_subject_name(s)).collect();
            doc.insert(format!("{}_key", subject_type), json!(keys));
        }
    }

    // last_modified_i
    let mut all_docs = vec![work.clone()];
    all_docs.extend(editions.iter().cloned());
    let last_modified_i = all_docs.iter().map(|d| crate::helpers::datetimestr_to_int(d.get("last_modified"))).max().unwrap_or(0);
    doc.insert("last_modified_i".to_string(), json!(last_modified_i));

    // ebook fields: skip IA -> UNCLASSIFIED if ocaid else NO_EBOOK
    #[derive(PartialEq, Eq, PartialOrd, Ord, Clone, Copy)]
    enum EbookAccess { NoEbook=0, Unclassified=1, PrintDisabled=2, Borrowable=3, Public=4 }
    impl EbookAccess {
        fn to_solr_str(self) -> &'static str {
            match self {
                EbookAccess::NoEbook => "no_ebook",
                EbookAccess::Unclassified => "unclassified",
                EbookAccess::PrintDisabled => "printdisabled",
                EbookAccess::Borrowable => "borrowable",
                EbookAccess::Public => "public",
            }
        }
    }
    fn edition_ebook_access(ed: &Value) -> EbookAccess {
        match ed.get("ocaid").and_then(|v| v.as_str()).map(|s| s.trim().to_string()).filter(|s| !s.is_empty()) {
            Some(_) => EbookAccess::Unclassified,
            None => EbookAccess::NoEbook,
        }
    }
    let edition_accesses: Vec<EbookAccess> = editions.iter().map(|ed| edition_ebook_access(ed)).collect();
    let work_ebook_access = edition_accesses.iter().cloned().max().unwrap_or(EbookAccess::NoEbook);
    let has_fulltext = work_ebook_access > EbookAccess::Unclassified;
    let public_scan_b = work_ebook_access == EbookAccess::Public;
    let ebook_count_i = edition_accesses.iter().filter(|&&a| a > EbookAccess::NoEbook).count() as i64;
    doc.insert("has_fulltext".to_string(), json!(has_fulltext));
    doc.insert("public_scan_b".to_string(), json!(public_scan_b));
    doc.insert("ebook_count_i".to_string(), json!(ebook_count_i));
    doc.insert("ebook_access".to_string(), json!(work_ebook_access.to_solr_str()));
    // ia and ebook_provider derived from ocaid
    let mut ia_list: Vec<(EbookAccess, String)> = Vec::new();
    for ed in editions {
        if let Some(ocaid) = ed.get("ocaid").and_then(|v| v.as_str()).map(|s| s.trim().to_string()).filter(|s| !s.is_empty()) {
            let acc = edition_ebook_access(ed);
            // sorting key: -acc value + goog preference
            ia_list.push((acc, ocaid));
        }
    }
    // sort by (-acc, is_goog)
    ia_list.sort_by(|a,b| {
        let ord = b.0.cmp(&a.0);
        if ord != std::cmp::Ordering::Equal { return ord; }
        let a_goog = a.1.ends_with("goog") as i32;
        let b_goog = b.1.ends_with("goog") as i32;
        a_goog.cmp(&b_goog)
    });
    if !ia_list.is_empty() {
        let ia_vals: Vec<String> = ia_list.iter().map(|(_,id)| id.clone()).collect();
        doc.insert("ia".to_string(), json!(ia_vals));
        // ebook_provider: uniq provider names where ocaid present -> ["ia"]
        let ebook_provider: Vec<String> = if ia_vals.is_empty() { vec![] } else { vec!["ia".to_string()] };
        if !ebook_provider.is_empty() {
            doc.insert("ebook_provider".to_string(), json!(ebook_provider));
        }
    }
    // cover_i etc: try work covers then editions
    let cover_i = work.get("covers").and_then(|v| v.as_array()).and_then(|arr| arr.iter().find_map(|v| v.as_i64().filter(|&x| x != -1))).or_else(|| {
        editions.iter().find_map(|ed| ed.get("covers").and_then(|v| v.as_array()).and_then(|arr| arr.iter().find_map(|v| v.as_i64().filter(|&x| x != -1))))
    });
    if let Some(c) = cover_i { doc.insert("cover_i".to_string(), json!(c)); }
    // cover_edition_key
    if let Some(c) = cover_i {
        let cek = editions.iter().find(|ed| ed.get("covers").and_then(|v| v.as_array()).map(|arr| arr.iter().any(|v| v.as_i64()==Some(c))).unwrap_or(false))
            .and_then(|ed| ed.get("key").and_then(|k| k.as_str()).map(|s| s.split('/').last().unwrap_or(s).to_string()));
        if let Some(k) = cek { doc.insert("cover_edition_key".to_string(), json!(k)); }
    }
    // by_statement
    let by_statement: HashSet<String> = editions.iter().filter_map(|ed| ed.get("by_statement").and_then(|v| v.as_str()).map(|s| s.to_string())).collect();
    if !by_statement.is_empty() { doc.insert("by_statement".to_string(), json!(by_statement.into_iter().collect::<Vec<_>>())); }
    // number_of_pages_median
    let pages: Vec<i64> = editions.iter().filter_map(|ed| {
        ed.get("number_of_pages").and_then(|v| {
            if let Some(s) = v.as_str() { s.parse::<i64>().ok() }
            else if let Some(n) = v.as_i64() { Some(n) }
            else { None }
        })
    }).collect();
    if !pages.is_empty() {
        let mut sorted = pages.clone(); sorted.sort_unstable();
        let median = if sorted.len() %2 ==1 { sorted[sorted.len()/2] as f64 } else { (sorted[sorted.len()/2 -1] as f64 + sorted[sorted.len()/2] as f64)/2.0 };
        let median_ceil = median.ceil() as i64;
        doc.insert("number_of_pages_median".to_string(), json!(median_ceil));
    }
    // format (physical_format)
    let formats: HashSet<String> = editions.iter().filter_map(|ed| ed.get("physical_format").and_then(|v| v.as_str()).map(|s| s.to_string())).collect();
    if !formats.is_empty() { doc.insert("format".to_string(), json!(formats.into_iter().collect::<Vec<_>>())); }
    // identifiers aggregated from editions (preserve Python's extend without cross-edition dedup, but per-edition uniq)
    {
        use std::collections::HashMap;
        let mut identifiers: HashMap<String, Vec<String>> = HashMap::new();
        // RE_SOLR_FIELD is static above
        for ed in editions {
            if let Some(id_map) = ed.get("identifiers").and_then(|v| v.as_object()) {
                for (key, val) in id_map {
                    let mut solr_key = key.replace(".", "_").replace(",", "_").replace("(", "").replace(")", "").replace(":", "_").replace("/", "").replace("#", "").to_lowercase();
                    if !RE_SOLR_FIELD.is_match(&solr_key) { continue; }
                    if let Some(arr) = val.as_array() {
                        // Python: uniq(v.strip() for v in id_list if v)  -> checks original truthiness before strip
                        let vals: Vec<String> = arr.iter().filter_map(|v| v.as_str()).filter(|s| !s.is_empty()).map(|s| s.trim().to_string()).collect();
                        // Note: Python keeps '' if original was ' ' -> trimmed '' kept
                        let uniq_vals = {
                            let mut seen = HashSet::new();
                            let mut out = Vec::new();
                            for v in vals {
                                if seen.insert(v.clone()) { out.push(v); }
                            }
                            out
                        };
                        if uniq_vals.is_empty() { continue; }
                        let entry = identifiers.entry(format!("id_{}", solr_key)).or_insert_with(Vec::new);
                        for v in uniq_vals { entry.push(v); }
                    }
                }
            }
        }
        for (k, v) in identifiers {
            if !v.is_empty() { doc.insert(k, json!(v)); }
        }
    }
    // lexile
    {
        let mut lex_set = HashSet::new();
        for ed in editions {
            if let Some(lex_str) = ed.get("lexile").and_then(|v| v.as_str()) {
                if let Ok(n) = lex_str.parse::<i64>() { lex_set.insert(n); }
            } else if let Some(n) = ed.get("lexile").and_then(|v| v.as_i64()) {
                lex_set.insert(n);
            }
        }
        if !lex_set.is_empty() {
            doc.insert("lexile".to_string(), json!(lex_set.into_iter().collect::<Vec<_>>() ));
        }
    }
    // ia_box_id (legacy)
    {
        let mut box_ids = HashSet::new();
        for ed in editions {
            if let Some(v) = ed.get("ia_box_id") {
                if let Some(s) = v.as_str() { box_ids.insert(s.to_string()); }
                else if let Some(arr) = v.as_array() {
                    for item in arr { if let Some(s) = item.as_str() { box_ids.insert(s.to_string()); } }
                }
            }
        }
        if !box_ids.is_empty() {
            doc.insert("ia_box_id".to_string(), json!(box_ids.into_iter().collect::<Vec<_>>() ));
        }
    }
    // chapter from editions table_of_contents
    let mut chapter_set = HashSet::new();
    for ed in editions {
        let olid = ed.get("key").and_then(|v| v.as_str()).map(|s| s.split('/').last().unwrap_or(s).to_string()).unwrap_or_default();
        if let Some(arr) = ed.get("table_of_contents").and_then(|v| v.as_array()) {
            for ch in arr {
                if let Some(s) = ch.as_str() {
                    chapter_set.insert(format!("{} | {}", olid, s));
                } else if let Some(obj) = ch.as_object() {
                    let label = obj.get("label").and_then(|v| v.as_str()).unwrap_or("");
                    let mut title = obj.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    if let Some(sub) = obj.get("subtitle").and_then(|v| v.as_str()) {
                        if !sub.is_empty() { title = format!("{}: {}", title, sub); }
                    }
                    if let Some(authors) = obj.get("authors").and_then(|v| v.as_array()) {
                        let names: Vec<String> = authors.iter().filter_map(|a| a.get("name").and_then(|v| v.as_str()).map(|s| s.to_string())).collect();
                        if !names.is_empty() { title = format!("{} ({})", title, names.join(", ")); }
                    }
                    let pagenum = obj.get("pagenum").and_then(|v| v.as_str()).unwrap_or("");
                    chapter_set.insert(format!("{} | {} | {} | {}", olid, label, title, pagenum));
                }
            }
        }
    }
    if !chapter_set.is_empty() { doc.insert("chapter".to_string(), json!(chapter_set.into_iter().collect::<Vec<_>>())); }
    // editions nested (simplified) - include minimal fields to match py full diff; skip usefulness scores for now but provide core
    if !editions.is_empty() {
        let mut edition_docs = Vec::new();
        for ed in editions {
            let mut ed_doc = serde_json::Map::new();
            if let Some(k) = ed.get("key").and_then(|v| v.as_str()) { ed_doc.insert("key".to_string(), json!(k)); }
            let work_keys_for_ed: Vec<String> = ed.get("works").and_then(|v| v.as_array()).map(|arr| arr.iter().filter_map(|w| w.get("key").and_then(|k| k.as_str()).map(|s| s.split('/').last().unwrap_or(s).to_string())).collect()).unwrap_or_default();
            if !work_keys_for_ed.is_empty() { ed_doc.insert("work_key".to_string(), json!(work_keys_for_ed)); }
            ed_doc.insert("type".to_string(), json!("edition"));
            if let Some(t) = ed.get("title").and_then(|v| v.as_str()) { ed_doc.insert("title".to_string(), json!(t)); }
            if let Some(s) = ed.get("subtitle").and_then(|v| v.as_str()) { ed_doc.insert("subtitle".to_string(), json!(s)); }
            // alternative_title from edition
            let mut alt = HashSet::new();
            if let Some(t) = ed.get("title").and_then(|v| v.as_str()) {
                let mut full = t.to_string();
                if let Some(sub) = ed.get("subtitle").and_then(|v| v.as_str()) { full = format!("{}: {}", full, sub); }
                alt.insert(full);
            }
            for wt in get_array_str(ed, "work_titles") { alt.insert(wt); }
            for ot in get_array_str(ed, "other_titles") { alt.insert(ot); }
            if !alt.is_empty() { ed_doc.insert("alternative_title".to_string(), json!(alt.into_iter().collect::<Vec<_>>())); }
            // chapter for edition
            let olid_ed = ed.get("key").and_then(|v| v.as_str()).map(|s| s.split('/').last().unwrap_or(s).to_string()).unwrap_or_default();
            let mut chap = Vec::new();
            if let Some(arr) = ed.get("table_of_contents").and_then(|v| v.as_array()) {
                for ch in arr {
                    if let Some(s) = ch.as_str() { chap.push(format!("{} | {}", olid_ed, s)); }
                    else if let Some(obj) = ch.as_object() {
                        let label = obj.get("label").and_then(|v| v.as_str()).unwrap_or("");
                        let mut title = obj.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        if let Some(sub) = obj.get("subtitle").and_then(|v| v.as_str()) { if !sub.is_empty() { title = format!("{}: {}", title, sub); } }
                        if let Some(authors) = obj.get("authors").and_then(|v| v.as_array()) {
                            let names: Vec<String> = authors.iter().filter_map(|a| a.get("name").and_then(|v| v.as_str()).map(|s| s.to_string())).collect();
                            if !names.is_empty() { title = format!("{} ({})", title, names.join(", ")); }
                        }
                        let pagenum = obj.get("pagenum").and_then(|v| v.as_str()).unwrap_or("");
                        chap.push(format!("{} | {} | {} | {}", olid_ed, label, title, pagenum));
                    }
                }
            }
            if !chap.is_empty() { ed_doc.insert("chapter".to_string(), json!(chap)); }
            if let Some(c) = ed.get("covers").and_then(|v| v.as_array()).and_then(|arr| arr.iter().find_map(|v| v.as_i64().filter(|&x| x!=-1))) { ed_doc.insert("cover_i".to_string(), json!(c)); }
            let langs = edition_languages(ed);
            if !langs.is_empty() { ed_doc.insert("language".to_string(), json!(langs)); }
            // author fields duplicated from work
            if let Some(v) = doc.get("author_name") { ed_doc.insert("author_name".to_string(), v.clone()); }
            if let Some(v) = doc.get("author_key") { ed_doc.insert("author_key".to_string(), v.clone()); }
            if let Some(v) = doc.get("author_facet") { ed_doc.insert("author_facet".to_string(), v.clone()); }
            if let Some(v) = doc.get("author_alternative_name") { ed_doc.insert("author_alternative_name".to_string(), v.clone()); }
            if let Some(n) = ed.get("edition_name").and_then(|v| v.as_str()) { ed_doc.insert("edition_name".to_string(), json!([n])); }
            let pubs = edition_publishers(ed);
            if !pubs.is_empty() { ed_doc.insert("publisher".to_string(), json!(pubs)); }
            if let Some(fmt) = ed.get("physical_format").and_then(|v| v.as_str()) { ed_doc.insert("format".to_string(), json!([fmt])); }
            if let Some(pd) = ed.get("publish_date").and_then(|v| v.as_str()) { ed_doc.insert("publish_date".to_string(), json!([pd])); }
            if let Some(py) = edition_publish_year(ed) { ed_doc.insert("publish_year".to_string(), json!([py])); }
            // isbn
            let isbns = edition_isbn(ed);
            if !isbns.is_empty() { ed_doc.insert("isbn".to_string(), json!(isbns)); }
            let lccns = get_array_str(ed, "lccn").into_iter().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect::<Vec<_>>();
            if !lccns.is_empty() { ed_doc.insert("lccn".to_string(), json!(lccns)); }
            let oclcs = get_array_str(ed, "oclc_numbers").into_iter().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect::<Vec<_>>();
            if !oclcs.is_empty() { ed_doc.insert("oclc".to_string(), json!(oclcs)); }
            // ia
            if let Some(ocaid) = ed.get("ocaid").and_then(|v| v.as_str()).map(|s| s.trim().to_string()).filter(|s| !s.is_empty()) {
                ed_doc.insert("ia".to_string(), json!([ocaid]));
                ed_doc.insert("ebook_access".to_string(), json!("unclassified"));
                ed_doc.insert("ebook_provider".to_string(), json!(["ia"]));
                ed_doc.insert("has_fulltext".to_string(), json!(false));
                ed_doc.insert("public_scan_b".to_string(), json!(false));
            } else {
                ed_doc.insert("ebook_access".to_string(), json!("no_ebook"));
                ed_doc.insert("has_fulltext".to_string(), json!(false));
                ed_doc.insert("public_scan_b".to_string(), json!(false));
            }
            // filtering empty? Keep
            // also need ia_collection, ia_box_id etc skipped (empty -> omitted)
            // usefulness scores: skip for now -> python will have them, but we omit; diff will still show py only for those scores, but we could provide dummy to reduce diff
            // Instead provide dummy usefulness scores to match py? Let's compute simple based on language? Simpler: omit and accept py only for those fields, but then editions diff will still be large because py editions have usefulness scores.
            // For minimal, we will keep sans scores; logical diff will flag py only for those scores within editions, but top-level editions count will be matched.
            // To make editions comparable, we should not compare inner usefulness scores if we omit them.
            // So we will insert placeholder scores same as py logic? Let's add static 0 to avoid py only counts if py expects scores.
            // Python's editions always have usefulness_score etc (see edition.py:498). Our omission will cause py only for those keys inside editions.
            // For now omit and note.
            edition_docs.push(Value::Object(ed_doc));
        }
        doc.insert("editions".to_string(), json!(edition_docs));
    }

    // text field minimal: title + subjects?
    // We'll leave out text for parity (Python builds via additional code not covered here). Skip.

    // Remove empty? Already skipped.

    Value::Object(doc)
}
