---
name: scansci-pdf-agent
description: Academic paper download agent with multi-source fallback. Use when user asks to download papers by DOI, search for papers, or batch download literature. Integrates scansci-pdf CLI (13+ sources, USTC WebVPN) with automatic fallback to web-access/browser automation for publishers that block direct download (AIP ePDF, MDPI Cloudflare, etc.). Triggers: "下载文献", "download paper", "批量下载", "fetch DOI", "search papers", "找论文".
version: 1.0.0
metadata:
  author: DY
  agent_created: true
---

# scansci-pdf-agent

Multi-source academic paper download agent with intelligent fallback chain.

## Architecture

```
DOI/Input → scansci-pdf fetch (WebVPN+OA+SciHub) 
          → if 403/ePDF/Cloudflare → web-access CDP browser
          → if still blocked → manual handoff to user
```

## Environment

- **Python venv**: `C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/binaries/python/envs/scansci2/`
- **Executable**: `scansci-pdf.exe` in `Scripts/` subdirectory
- **Config**: `~/.scansci-pdf/config.json` (WebVPN: USTC, enabled)
- **Cookies**: `~/.scansci-pdf/cookies/webvpn-cookies.json`
- **Known bug fix**: `auth.py` patched for `vpnsci_base_url` / `vpnsci_cookie_file` config key mapping

## Commands

### Search papers

```bash
SCANSCI="C:/Users/Administrator.DESKTOP-7RU274I/.workbuddy/binaries/python/envs/scansci2/Scripts/scansci-pdf.exe"
$SCANSCI search "Dyson orbital photoionization" --limit 10 --sort cited_by_count
```

### Download single paper (WebVPN cascade)

```bash
$SCANSCI fetch <DOI> --output "<output_dir>"
```

The `fetch` command runs a 7-step institutional cascade:
1. Unpaywall OA check
2. DOI resolution
3. Publisher PDF URL construction
4. WebVPN proxy download (with saved cookies)
5. CloakBrowser fallback
6. CARSI federated access
7. Browser login handoff

### Download single paper (all-source racing)

```bash
$SCANSCI get <DOI> --strategy oa_first --output "<output_dir>"
```

Strategies: `fastest` (default), `oa_first`, `legal_only`, `scihub_only`, `scihub_first`

### Batch download

```bash
$SCANSCI batch --file <doi_list.txt> --output "<output_dir>"
```

### WebVPN login (when cookies expire)

```bash
$SCANSCI login --login-type webvpn
```

Opens a browser window. User must complete USTC CAS login manually. Cookies auto-saved.

### Check status

```bash
$SCANSCI check          # dependency check
$SCANSCI browser-status # CloakBrowser status
$SCANSCI config-cmd     # show config
```

## Publisher Compatibility (tested 2025-07)

| Publisher | fetch result | Notes |
|-----------|-------------|-------|
| arXiv | ✅ direct | No anti-scraping |
| Nature | ✅ direct | NatureDirect source |
| IOP | ✅ direct | Unpaywall OA source |
| ACS | ✅ WebVPN | USTC WebVPN institutional access |
| AIP | ❌ ePDF 403 | Needs browser fallback |
| MDPI | ❌ Cloudflare 403 | Needs browser fallback |

## Fallback Chain for Blocked Publishers

When `scansci-pdf fetch` returns `auth_required` or 403 for AIP/MDPI/other blocked publishers:

### Step 1: Check if paper is OA via alternative route

```bash
# Check OpenAlex for OA URL
python -c "
import urllib.request, ssl, json
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
url = 'https://api.openalex.org/works/doi:<DOI>'
req = urllib.request.Request(url, headers={'User-Agent':'scansci/1.0'})
data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=10).read())
oa = data.get('open_access',{})
print(f'OA: {oa.get(\"oa_status\")} URL: {oa.get(\"oa_url\")}')
"
```

### Step 2: Use web-access skill (CDP browser automation)

Load the `web-access` skill and use CDP to:
1. Navigate to the publisher page through USTC WebVPN
2. The user's Chrome may already have institutional login
3. Find and click the PDF download button
4. Save the PDF to the output directory

```bash
# Construct WebVPN URL for the publisher
# Use scansci-pdf's convert_url function or manual construction:
# https://wvpn.ustc.edu.cn/<scheme>/<encrypted_hostname><path>
```

### Step 3: Use playwright-cli skill

If web-access CDP is not available, use `playwright-cli` skill for browser automation:
1. Launch headless or headed browser
2. Navigate to publisher page via WebVPN
3. Wait for page load, find PDF link
4. Download PDF

### Step 4: Use ustc-literature-downloader skill

For USTC-specific institutional access through Chrome's logged-in session:
1. Check if Chrome has active USTC WebVPN session
2. Use CDP to navigate and download
3. This skill handles USTC SSO, WebVPN, and publisher-specific workflows

### Step 5: Manual handoff

If all automated methods fail:
1. Inform the user which DOI failed and which publisher blocked it
2. Provide the direct URL for manual download
3. Suggest the user download in their browser and place the file in the output directory
4. Offer to rename and organize the file once downloaded

## File Naming Convention

All downloaded papers should be renamed to:
```
Author1_Year_ShortTitle_JournalAbbrev.pdf
```

Examples:
- `Moitra_2021_EOMCC_Dyson_TDDFT_JCTC.pdf`
- `Ruberti_2019_RCS_ADC_Bspline_JCTC.pdf`
- `Gozem_2015_photoelectron_wavefunction_JPCL.pdf`

## Directory Organization

Papers should be sorted into topic-based subdirectories:
- `papers/B_spline_continuum/` — B-spline methods
- `papers/GTO_continuum/` — GTO L2 methods
- `papers/complex_scaling/` — ECS/complex scaling
- `papers/spherical_wave/` — Spherical wave basis
- `papers/general_review/` — Reviews and general references

## Cookie Maintenance

USTC WebVPN session cookies are session-type (expire when browser closes).
If downloads fail with "All saved cookies have expired":
1. Re-run `$SCANSCI login --login-type webvpn`
2. Complete USTC CAS login in the browser window
3. Cookies auto-saved to `~/.scansci-pdf/cookies/webvpn-cookies.json`
4. Retry the download

## MCP Integration

The scansci-pdf MCP server is configured in `~/.workbuddy/mcp.json` as `scansci_pdf`.
If MCP tools are discoverable via ToolSearch, use:
- `mcp__scansci_pdf__scansci_pdf_smart_download` — zero-config download
- `mcp__scansci_pdf__scansci_pdf_search` — paper search
- `mcp__scansci_pdf__scansci_pdf_batch_download` — batch download
- `mcp__scansci_pdf__scansci_pdf_login` — institutional login

If MCP tools are NOT discoverable (known issue), use CLI commands directly.
