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

## Finding its way to the full list

Unions rarely put their societies at a predictable address. Stirling, for example, keeps societies at `/sports-and-societies/societies/a-z-of-societies/` and its 50+ sports clubs on a *separate* A-Z page.

So if SocietyScout lands on a home page or a hub page, it looks for links worded like "A-Z of Societies", "All Clubs", "Sports Clubs" or "Student Groups" and follows them to the real list. It follows several, because societies and sports clubs are usually listed apart, and both are collected: sports teams and course societies included.

It also treats a union's sub-domains as one site, so a union split across `su.example.ac.uk` and `example.ac.uk` is crawled properly rather than stopping at the boundary.

The union's own entry is never recorded as a society, while societies whose names contain "Union" — Christian Union, Debating Union — are kept.

## Checking it against real union websites

```
.venv\Scripts\python scraper.py --selftest          (Windows)
.venv/bin/python scraper.py --selftest               (Mac)
```

It runs against two real students' union sites on different website platforms and scores the three things that matter: whether it opened the individual society pages rather than just the listing, whether it was blocked, and whether any union-wide contact detail ended up attached to a society. Add your own page with `--selftest https://<union-site>/societies` to check that one too.

If anything comes back FAIL, send me the output.

## When a search finds nothing

Run the diagnostic. It walks the whole chain and tells you exactly which link breaks:

```
.venv/bin/python scraper.py --diagnose "University of Leeds"          (Mac)
.venv\Scripts\python scraper.py --diagnose "University of Leeds"      (Windows)
```

Or point it straight at a page: `--diagnose https://<union-site>/activities`

It checks your internet, whether web search is working, which union site it found, what that site's robots.txt allows, whether the page actually contains society links, and then does a small live run. The four things it usually turns up:

1. **Web search returned nothing.** Free search blocks repeated automated use. Wait ten minutes, or skip searching altogether by passing the societies page with `--url`.
2. **The page is empty without JavaScript.** Some unions build their society list in the browser, so there is nothing in the page to read. The site map is tried automatically; if that fails too, those societies need adding by hand.
3. **The page is off limits in robots.txt.** Some unions allow their individual society pages but not their A-Z listing. SocietyScout then works from the site map instead. If everything is off limits, that union has asked bots not to read it, and the answer is to ask a person.
4. **The page loaded but nothing matched.** Send me the diagnostic output and I'll adjust the patterns for that union.

Passing `--url` with the union's own Societies or Clubs A-Z page is the single most reliable thing you can do. It skips searching entirely, and SocietyScout remembers the page for next time.

## How it behaves towards the websites it reads

It reads pages the way a slow, honest visitor would, and it never pretends to be someone else.

- **It says what it is.** Every request carries a User-Agent naming SocietyScout and a `From` header with your contact email, so any site owner can see who is visiting and get in touch.
- **It obeys robots.txt**, including each site's own requested crawl delay.
- **It asks the site where its pages are.** It reads the site map rather than guessing at addresses. Guessing produces bursts of "not found" responses, which is what makes a firewall treat a visitor as a scanner.
- **It waits** about two seconds between requests to the same site, with a little randomness so it isn't a heavy, metronomic load.
- **It stops when told.** A 429 or 503 means wait and slow down; a 401 or 403 means stop asking that site altogether for the rest of the run. It doesn't argue and doesn't retry from a different angle.
- **It only reads.** GET requests for ordinary pages. It never logs in, submits forms, or touches pages robots.txt puts off limits.
- **It remembers pages it has read**, in `data/page_cache`, so running a search twice doesn't ask the site twice.
- **It keeps a record.** `data/fetch_log.csv` lists every page requested and what came back, so if a university ever asks what you did, you can show them exactly.

### Keeping the union's own pages out of the CSV

On its way to the lists the crawler passes through the union's own pages — the home page, "Get Involved", "What's On". Those used to end up in the CSV as if they were societies, carrying the union's switchboard and info address.

A page is now only recorded as a society if the union's own list linked to it, or if it carries contact details of its own once the union's have been taken out. That test is structural rather than a list of page titles, so it holds on unions whose pages are named something I've never seen.

### Telling the union's contacts apart from a society's

Every union page carries the union's own address and switchboard in its header and footer, and those must not end up in your outreach list as if they were a society's. Three things keep them out:

1. Contact details found in a page's header, footer or menus are recorded as the union's and excluded from every society.
2. Addresses of the form `theunion@`, `societies.union@`, `su@` and the like are never used as a society's contact, wherever they appear on the page.
3. Any detail that turns up across a large share of societies is treated as site-wide and removed.

A society with no address of its own is left with an empty Email and a note saying so. That is deliberate: a blank cell is more useful than the union office's address pretending to be a society's.

### If a site blocks you anyway

`data/coverage.csv` marks it, and the honest options are:

SocietyScout now tells the difference between a site saying no and a bot-protection challenge (Cloudflare and similar), and names which it hit. A challenge page is a wall in front of everything automated, not a judgement about this tool, and no amount of polite retrying gets through it. The options are:

1. **Slow down.** Run that university on its own with `--delay 5`, outside busy hours.
2. **Skip the crawl entirely.** Most unions publish a societies list you can read yourself; add those entries by hand.
3. **Ask.** A short email to the union's activities or membership officer explaining that you're a clothing brand wanting to contact societies about kit often gets you a better list than any scraper, and sometimes an introduction.

### What this tool will not do

It won't disguise itself as a web browser, rotate IP addresses or use proxy services, work around CAPTCHAs or bot protection, or ignore robots.txt. Those are ways of getting past a decision a site has made about who may visit, which puts you outside its terms of use and potentially on the wrong side of the Computer Misuse Act. A blocked site is a "no", and the answer is to ask a person, not to try a different disguise.

## Using the data responsibly

Being publicly posted doesn't exempt contact details from UK GDPR. When you use them:

- Stick to the emails societies publish for enquiries.
- Say who you are and where you found their details.
- Include a way to opt out.
- Delete contacts you no longer need.
