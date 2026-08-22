use once_cell::sync::Lazy;
use regex::Regex;

static ARTICLE_PATTERN: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^(an? |the |l[aeo]s? |l'|de la |el |il |un[ae]? |du |de[imrst]? |das |ein |eine[mnrs]? |bir )(.*)",
    )
    .unwrap()
});

pub fn sort_title(title: &str, subtitle: Option<&str>) -> String {
    let mut full = title.to_string();
    if let Some(sub) = subtitle {
        if !sub.is_empty() {
            full = format!("{}: {}", full, sub);
        }
    }
    let lower = full.to_lowercase();
    // need case-insensitive match but preserve original case for output? Python uses re.IGNORECASE but returns original case groups.
    // Use regex with (?i)
    static RE_I: Lazy<Regex> = Lazy::new(|| {
        Regex::new(
            r"(?i)^(an? |the |l[aeo]s? |l'|de la |el |il |un[ae]? |du |de[imrst]? |das |ein |eine[mnrs]? |bir )(.*)",
        )
        .unwrap()
    });
    if let Some(caps) = RE_I.captures(&full) {
        let article = caps.get(1).unwrap().as_str().trim().to_string();
        let rest = caps.get(2).unwrap().as_str().to_string();
        return format!("{}, {}", rest, article);
    }
    full
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_sort_title() {
        assert_eq!(sort_title("The Great Gatsby", None), "Great Gatsby, The");
        assert_eq!(sort_title("A Tale of Two Cities", None), "Tale of Two Cities, A");
        assert_eq!(sort_title("Great Gatsby", None), "Great Gatsby");
        assert_eq!(sort_title("The Title", Some("Subtitle")), "Title: Subtitle, The");
    }
}
