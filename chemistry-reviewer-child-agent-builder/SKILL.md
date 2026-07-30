---
name: chemistry-reviewer-child-agent-builder
description: "Builds journal-specific child reviewers from the chemistry-reviewer mother agent. Handles: Phase A journal ISSN crawl with container-title validation, Phase B landmark paper selection, CDP-browser PDF download with institutional access, YEAR-AUTHOR-TITLE file renaming, literature_database.json construction, and IMA knowledge base upload. Trigger when creating a new child expert or rebuilding an existing one's database."
agent_created: true
---

# Chemistry Reviewer Child Agent Builder

Builds a complete journal-specific child reviewer by following a deterministic pipeline: journal metadata collection → literature database construction → PDF download → file organization → DB serialization → IMA deployment. Each step has known failure modes and prescribed remedies.

---

## 1. Scrape Journal Metadata

1. Fetch the target journal's **Aims & Scope** page (typically `nature.com/<journal>/about/aims` or equivalent). Extract: scope description, article types accepted, review criteria.
2. Fetch the **Submission Guidelines** page. Extract: word limits, figure limits, reference limits, formatting requirements.
3. Fetch the **Editorial Policies** page. Extract: peer review process, reviewer guidelines, acceptance rate indicators.
4. Record the journal's **ISSN** (for Crossref API calls) and its **canonical name list** (for journal validation):
   ```python
   TARGET_JOURNAL_NAMES = [
       "Nature Astronomy",
       "Nat. Astron.",
       "Nat Astron",
       "Nature Astronomy (Nat. Astron.)",
   ]
   ```

---

## 2. Build Phase A Literature Database (Journal-Specific, Minimum 60 Papers)

### 2.1 API Crawl

Use the Crossref API with ISSN filtering. Run 20-30 keyword queries covering all chemistry sub-topics within the journal's scope. Rate-limit: 1 request per 0.35 seconds.

```
GET https://api.crossref.org/works?query=<keyword>&filter=issn:<ISSN>,from-pub-date:<start-date>&rows=20&sort=published&order=desc
```

### 2.2 Strict Journal Source Validation (CRITICAL)

For every paper returned by the Crossref API, extract the **`container-title`** field and validate it against the target journal's canonical name list (case-insensitive). Papers whose `container-title` does NOT match MUST be excluded — even if they appeared in ISSN-filtered results (Crossref cross-linking can introduce non-target papers).

```python
containers = item.get('container-title', [])
journal = containers[0] if containers else ''
if not any(name.lower() in journal.lower() for name in TARGET_JOURNAL_NAMES):
    continue  # EXCLUDE: not from target journal
```

Do NOT rely on keyword filtering to catch journal mismatches. The `container-title` field is authoritative.

### 2.3 Chemistry-Relevance Filtration

After journal validation, apply keyword filtering to retain chemistry-relevant papers. A paper should match at least 2 keywords from the chemistry term list (molecule, chemical, organic, spectroscopic, ice, dust, carbon, nitrogen, oxygen, hydrogen, isotope, prebiotic, comet, asteroid, planet, atmosphere, exoplanet, volatile, haze, silicate, grain, surface, disk, PAH, etc.).

### 2.4 Minimum Count Enforcement

Target: **≥60 papers**. If fewer than 60 papers pass journal validation:
- **First expansion**: Extend search window from 3-5 years to 7 years
- **Second expansion**: Broaden keyword queries to cover more sub-topics
- **Third expansion**: Extend to 10 years
- **NEVER** include non-target-journal papers to meet the minimum

### 2.5 Post-Selection Audit

Run an automated check after selection:
```python
non_target = [p for p in phase_a_papers if not any(name.lower() in p["journal"].lower() for name in TARGET_JOURNAL_NAMES)]
assert len(non_target) == 0, f"Phase A contains {len(non_target)} papers not from target journal!"
```

---

## 3. Build Phase B Landmark Database (Cross-Journal, Minimum 25 Papers)

### 3.1 Scope
Cover the full history of the field (typically 1999-present), spanning ALL major journals — not restricted to the target journal.

### 3.2 Selection Criteria
- **High-citation reviews** (>200 citations): Foundation papers defining the field
- **Seminal discoveries**: First detections, breakthrough mechanisms, paradigm shifts
- **Method/technique standards**: Widely-adopted databases (HITRAN, UMIST, etc.), benchmark methods
- **Recent breakthroughs**: Papers likely to become landmarks
- Use the `literature-review` skill to search for candidates

### 3.3 Minimum Threshold: **≥25 papers**

---

## 4. Download PDFs via CDP Browser

### 4.1 Prerequisites
- User must have their Chrome browser open with `--remote-debugging-port=9222`
- The browser must have institutional access to download paywalled papers
- Load the `web-access` skill and follow its CDP connection workflow

### 4.2 CDP Download Workflow

```python
PROXY = 'http://localhost:3456'

# Step 1: Open DOI page in browser tab to get article URL
resp = urllib.request.urlopen(urllib.request.Request(f'{PROXY}/new', data=doi.encode(), method='POST'), timeout=20)
target = json.loads(resp.read())['targetId']
time.sleep(5)

# Step 2: Get current URL after redirect
resp2 = urllib.request.urlopen(urllib.request.Request(f'{PROXY}/eval?target={target}', data=b'location.href', method='POST'), timeout=10)
article_url = json.loads(resp2.read()).get('value', '')

# Step 3: Build PDF URL from article path
article_id = article_url.split('/articles/')[-1].split('?')[0].split('#')[0]
pdf_url = f'https://www.nature.com/articles/{article_id}.pdf'

# Close the article tab
urllib.request.urlopen(urllib.request.Request(f'{PROXY}/close?target={target}', method='POST'), timeout=5)

# Step 4: Open PDF URL directly in new tab
resp3 = urllib.request.urlopen(urllib.request.Request(f'{PROXY}/new', data=pdf_url.encode(), method='POST'), timeout=20)
target2 = json.loads(resp3.read())['targetId']
time.sleep(3)

# Step 5: Download via XHR (XMLHttpRequest, synchronous, bypasses CORS)
js_xhr = f'''(function() {{
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '{pdf_url}', false);
    xhr.overrideMimeType('text/plain; charset=x-user-defined');
    xhr.send();
    var data = xhr.responseText;
    var all = '';
    for (var c = 0; c < data.length; c += 30000) {{
        var chunk = data.substring(c, Math.min(c+30000, data.length));
        var bin = '';
        for (var j = 0; j < chunk.length; j++) bin += String.fromCharCode(chunk.charCodeAt(j) & 0xff);
        all += btoa(bin) + '|';
    }}
    return JSON.stringify({{size: data.length, chunks: all}});
}})()'''

resp4 = urllib.request.urlopen(urllib.request.Request(f'{PROXY}/eval?target={target2}', data=js_xhr.encode(), method='POST'), timeout=300)
dl = json.loads(json.loads(resp4.read()).get('value', '{}'))

# Step 6: Reassemble and save
chunks = [c for c in dl['chunks'].split('|') if c]
pdf_bytes = b''.join(base64.b64decode(c) for c in chunks)
```

### 4.3 XHR Timeout Handling
If the CDP proxy returns HTTP 500 for large PDFs (>3MB), try:
- Reduce chunk size to 10000 bytes instead of 30000
- Increase eval timeout to 300 seconds
- For persistently failing PDFs, ask the user to manually download and place in the `pdfs/` folder

### 4.4 File Naming Convention (CRITICAL)

Every PDF MUST be saved as: **`YYYY-FirstAuthor-Title.pdf`**

Rules:
- Year: 4-digit publication year
- First Author: Last name only. Hyphenate multi-word names (e.g., `Salazar-Manzano`).
- Title: Truncated to 80 characters maximum. Remove special characters (`<>:"/\|?*`). Replace spaces with spaces (not underscores).
- DOI: When the PDF content is FROM a different paper than the filename indicates (cross-labeling), USE THE FILENAME as the ground truth after verifying the file's actual content.
- Cross-label detection: After renaming, run a content check to ensure the first page's title text matches the filename. If mismatched, either rename cycle or re-download.

---

## 5. Build and Write literature_database.json

### 5.1 Extract DOIs from PDFs
Use pdfminer to extract text from the first 2 pages of each PDF and search for a DOI pattern:
```python
import re
m = re.search(r'10\.\d{4,}/[a-zA-Z0-9_\.\-/()]+', text)
```

### 5.2 Look Up Metadata via Crossref
For each extracted DOI, call the Crossref API to get title, authors, year, journal, citation count. Rate-limit to 1 request per 0.35 seconds.

### 5.3 Fallback for Missing DOIs
If a PDF has no extractable DOI (some older papers or arXiv papers), use the filename to infer metadata: extract year and author from the filename, use the title from the filename's title portion. Mark `"relevance"` as `"Filename-based entry"`.

### 5.4 Database JSON Schema
```json
{
  "journal": "<Journal Name>",
  "issn": "<ISSN>",
  "date_compiled": "<YYYY-MM-DD>",
  "description": "...",
  "total_papers": <N>,
  "phase_a": { "count": <N>, "years": "<range>", "source": "Crossref API ISSN + container-title validation" },
  "phase_b": { "count": <N>, "years": "<range>", "source": "Multi-engine search + expert curation" },
  "topic_distribution": { "<topic>": <N> },
  "papers": [
    {
      "id": <N>,
      "title": "<Title>",
      "doi": "https://doi.org/<DOI>",
      "year": <YYYY>,
      "authors": "Author1; Author2",
      "journal": "<Journal>",
      "citationCount": <N>,
      "phase": "A|B",
      "relevance": "<Description>"
    }
  ]
}
```

### 5.5 Deduplication
After building the paper list from all PDFs, deduplicate by DOI. If two entries share the same DOI, keep only one (prefer the one with more complete metadata from Crossref).

---

## 6. Cross-Label Detection & Fix

After renaming all files and building the DB, run a content verification:

1. Extract text from the first page of each PDF
2. Check if key words from the filename's title appear in the extracted text
3. If title keywords have 0 hits while the filename has ≥2 significant keywords: **cross-label detected**
4. Fix by either:
   - **Cyclic rename**: If files A→B→C form a content shift chain, rename using temp files
   - **Re-download**: If the content is simply wrong with no matching file to swap

---

## 7. Upload to IMA Knowledge Base

### 7.1 Connect ima-mcp
Use the `ima-mcp` connector to access the IMA OpenAPI. The `knowledge_base` and `notes` sub-skills provide the API calls.

### 7.2 Upload PDFs
For each PDF:
1. **Preflight check**: Run the `preflight-check.cjs` script to validate the file
2. **Check duplicates**: Use `openapi/wiki/v1/check_repeated_names`
3. **Create media**: Use `openapi/wiki/v1/create_media` to get COS upload credentials
4. **Upload to COS**: Use the `cos-upload.cjs` script with the credentials
5. **Register knowledge**: Use `openapi/wiki/v1/add_knowledge` to link the uploaded file to the KB

### 7.3 Knowledge Base Setup
- Create a shared knowledge base named: **"Reviewer of <Journal Abbreviation>"**
- Ensure the KB is set to shared mode for team access
- Also store the `literature_database.json` (converted to .txt for IMA compatibility) in the KB

---

## 8. After the DB is Built: Update the Child Agent

1. Read the mother agent's `agents/chemistry-reviewer.md` as the foundation
2. Override scoring weights:
   - **General chemistry journals**: Novelty 25%, Significance 20%, Methodology 20%, Data 15%, Literature 10%, Presentation 10%
   - **Nature Astronomy / high-impact**: Novelty 30%, Significance 25%, Presentation 5%
3. Adjust scoring baseline to match journal selectivity
4. Add journal-specific review preferences and word limit checks
5. Inject the literature database path in the child agent's configuration
6. Register the child expert via `register_expert.py`

---

## Known Failure Modes & Remedies

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| CDP `/eval` returns HTTP 500 | XHR download timeout for large PDFs | Reduce chunk size to 10000; increase eval timeout to 300s; as last resort ask user to manually download |
| CDP `/new` returns HTTP 400 | Rate limiting or URL encoding issue | Add 2-second delay between requests; verify URL encoding |
| Crossref returns 0 results | ISSN mismatch or too narrow keywords | Verify ISSN; broaden keyword queries; extend date range |
| DOI extracted but Crossref lookup fails | DOI is malformed or not in Crossref | Use filename fallback; try OpenAlex API as alternative |
| Container-title mismatch | Crossref returned cross-linked papers | Strengthen validation: check ALL items, not just primary |
| File rename creates duplicates | Previous script matched by wrong field | Use exact article_id matching (full `s41550-xxx-yyyyy` pattern), never match by DOI suffix |
