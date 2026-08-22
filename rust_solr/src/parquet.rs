use anyhow::Result;
use arrow::array::{Int64Array, StringArray};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use parquet::arrow::ArrowWriter;
use std::fs::File;
use std::sync::Arc;

pub fn write_gold(docs: &[serde_json::Value], out_path: &str) -> Result<()> {
    let keys: Vec<String> = docs.iter().map(|d| d.get("key").and_then(|v| v.as_str()).unwrap_or("").to_string()).collect();
    let doc_jsons: Vec<String> = docs.iter().map(|d| serde_json::to_string(d).unwrap()).collect();
    let titles: Vec<Option<String>> = docs.iter().map(|d| d.get("title").and_then(|v| v.as_str()).map(|s| s.to_string())).collect();
    let edition_counts: Vec<Option<i64>> = docs.iter().map(|d| d.get("edition_count").and_then(|v| v.as_i64())).collect();

    let schema = Arc::new(Schema::new(vec![
        Field::new("key", DataType::Utf8, false),
        Field::new("doc_json", DataType::Utf8, false),
        Field::new("title", DataType::Utf8, true),
        Field::new("edition_count", DataType::Int64, true),
    ]));

    let batch = RecordBatch::try_new(
        schema.clone(),
        vec![
            Arc::new(StringArray::from(keys)),
            Arc::new(StringArray::from(doc_jsons)),
            Arc::new(StringArray::from(titles)),
            Arc::new(Int64Array::from(edition_counts)),
        ],
    )?;

    let file = File::create(out_path)?;
    let mut writer = ArrowWriter::try_new(file, schema, None)?;
    writer.write(&batch)?;
    writer.close()?;
    Ok(())
}
