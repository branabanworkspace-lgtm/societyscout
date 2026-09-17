# SocietyScout

Finds contact details that university societies have posted publicly, on students' union pages and society websites, and saves them to CSV. It runs on your own computer, with no AI and no subscription.

## Before you start

Open `scraper.py` in any text editor, find `CONTACT_EMAIL` near the top, and put your outreach email there. Websites see it with each visit, so they know who is reading their pages.

## Start it

- **Windows:** double-click `start_windows.bat`.
- **Mac:** right-click `start_mac.command` and choose Open. You only need to right-click the first time, because macOS checks files downloaded from the internet.

The first start sets things up, which takes a minute or two. Then SocietyScout opens in your browser. Keep the black window open while you use it.

If Python isn't installed yet, get it from [python.org/downloads](https://www.python.org/downloads/) first. On Windows, tick "Add python.exe to PATH" in the installer.

## Using it

1. Type a university name and press **Find societies**.
2. For the best results, also paste the union's Societies A-Z page into "Societies page address". The web search is free, but it occasionally picks the wrong page or gets rate-limited.
3. Results save automatically:
   - `exports/societies-<university>-<date>.csv` has that university's societies.
   - `data/societies_master.csv` has everything, with no duplicates.
4. In **Saved societies**, you can fix details, set the Status (Needs checking, Checked, Contacted), delete rows, and download.

## Every UK university at once

`uk_universities.csv` holds all 166 UK institutions with degree-awarding powers, taken from the government's recognised bodies list. Open the **All UK universities** tab and press Start, or run:

```
.venv/bin/python scraper.py --all                      (Mac)
.venv\Scripts\python scraper.py --all                  (Windows)
```

It works through the whole list, finding each union's societies page by itself. A full run takes several hours because it waits a second between pages.

- **Stop any time.** Progress is saved after each university. Running it again carries on where it left off, and retries the ones that didn't work.
- `--nation Scotland` does one nation at a time. `--coverage` shows how far you've got.
- `data/coverage.csv` records how each university went, so you can see which ones still need a hand.

For any university the search can't crack, open its union website, find the Societies A-Z page, and paste it into the Search tab. SocietyScout remembers it in `data/union_sites.csv` and uses it from then on.

## Specific universities only

List them in a text file, one per line (see `universities_example.txt`). Then open a terminal in this folder and run:

```
.venv/bin/python scraper.py --file universities_example.txt        (Mac)
.venv\Scripts\python scraper.py --file universities_example.txt    (Windows)
```

For a single university: `scraper.py "University of Leeds"`. For a company's staff clubs: `scraper.py "Company name" --company`.

## In Jupyter or Colab

Put `scraper.py` in the notebook's folder, then:

```python
!pip install requests beautifulsoup4 phonenumbers ddgs
from scraper import find_societies

find_societies("University of Leeds")
find_societies("University of Leeds", url="https://<union site>/societies")
```

The CSVs are saved in the notebook's current folder. The point-and-click app doesn't run inside notebooks; use the start files for that.

## Searching deeper

By default, when a society's union page shows no email, SocietyScout looks further:

1. The society's own website, if its union page links to one.
2. Its Linktree or similar link page.
3. Targeted web searches for that society at that university.

Anything found this way is checked against the society's name before it's kept, and a page that names a different university is rejected. The Notes column records which website each extra detail came from, so you can see what came from where.

It roughly doubles how long a university takes, so you can switch it off with the tickbox, or leave off `--deep` on the command line.

Instagram, Facebook and TikTok are deliberately not opened. They need a login, block automated visits, and their terms forbid it. Their links are still collected so you can open them yourself.

## What it collects

- Society name and type
- Emails, including ones hidden from spam bots
- Phone numbers
- Instagram and other social links
- Committee names with roles, when a page lists them (for example "President: Jane Smith")
- The society's own website, where its union page links to one

Kit, merch and sponsorship roles are flagged in Notes, since those people usually handle clothing orders.

## Limits

- It only reads public web pages. It doesn't log in anywhere, and it doesn't scrape Instagram or Facebook themselves.
- The bundled list covers every UK university, but each one's societies still have to be found on its own website. Unions use many different website platforms, so expect a portion to need their societies page pasted in once. `data/coverage.csv` tells you exactly which.
- Some union sites build their society list with JavaScript. SocietyScout tries the standard societies-page addresses and then the site map. If both fail, paste the page in or add those societies by hand.
- Some institutions on the list (conservatoires, private and distance-learning providers) have few societies or none.
- Committee names are picked out by pattern, so check them before you use them.
- It follows each site's robots.txt and waits a second between pages. Leave those on, or sites may block you.

## Optional: steadier web search

Get a Brave Search API key and set it as the `BRAVE_API_KEY` environment variable. As of mid-2026, Brave gives $5 of free credit a month (roughly 1,000 searches), needs a card on file, and asks you to credit Brave on your website. Check their current terms.

## Running it online

You can host this on Streamlit Community Cloud so the team uses it in a browser with nothing to install. See `DEPLOY.md` for the steps. The short version: a hosted copy loses its saved data whenever it restarts, so run the big jobs on a laptop and use the hosted app for lookups and hand-added entries.

## Using the data responsibly

Being publicly posted doesn't exempt contact details from UK GDPR. When you use them:

- Stick to the emails societies publish for enquiries.
- Say who you are and where you found their details.
- Include a way to opt out.
- Delete contacts you no longer need.
