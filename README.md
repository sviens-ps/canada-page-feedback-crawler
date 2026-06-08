# Canada.ca Page Feedback Crawler

Manual Python crawler that:

- reads these sitemap sources:
  - https://www.canada.ca/en/public-safety-canada.sitemap.xml
  - https://www.canada.ca/en/services.sitemap.xml
- keeps English pages only
- limits Services pages to:
  - /en/services/defence/nationalsecurity
  - /en/services/defence/securingborder
  - /en/services/policing
- checks for the English page feedback widget snippet
- exports results to CSV

## Output columns

- Title
- URL
- Page Feedback Yes/No

## Run locally

### Windows PowerShell
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python src/crawl_feedback.py
