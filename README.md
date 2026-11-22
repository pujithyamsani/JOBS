# Data Engineer job board

A searchable website of Data Engineer jobs in India that updates itself, for free. Every 2 hours from 6:45 AM to 10:45 PM India time, GitHub reads the job lists behind companies' own career pages and republishes the site. Your laptop can be off.

**Site:** https://pujithyamsani.github.io/JOBS/

## Where the jobs come from

Many companies run their career pages on Greenhouse, Lever, Ashby or SmartRecruiters. Each of these publishes a free, public list of the company's open jobs, with no sign-up or key needed. `config.json` lists 48 companies hiring in India, including Databricks, Snowflake, MongoDB, Stripe, Bosch, ServiceNow, Paytm, Meesho, CRED, Freshworks, Sigmoid and Fivetran.

Because these lists come straight from the company:

- **Closed jobs disappear:** when a company closes a job, it drops off the board at the next update.
- **Coverage is limited:** only those companies are included. Large service firms (TCS, Infosys, Accenture, Deloitte and others) and Naukri or LinkedIn postings aren't, because they don't offer a free open list. Keep using those sites too.

**Optional:** if you ever get an [Adzuna](https://developer.adzuna.com) key, add it as the repository secrets `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. The board then also collects Adzuna's listings, which cover many more companies. Without the key, nothing changes.

## What the site does

- **Search** by job title, company or skill. Press `/` to jump to the search box.
- **Filter** by city (Hyderabad, Bengaluru, Pune, Chennai, Gurugram, Noida, Remote, other cities) and by when the job was posted.
- **Seniority:** senior and lead roles are hidden by default. Turn the switch off to see them.
- **Experience:** jobs asking for 2+ years are hidden by default. Use the dropdown to hide jobs asking 3+, 4+ or 6+ years instead, or to show all. Jobs that don't mention years of experience are always shown.
- **New jobs** since your last visit get a gold **New** label and have their own tab.
- **Save**, **Applied** and **Hide** buttons keep your own lists. They're stored in the browser you use.

## Files

| File | What it does |
|---|---|
| `index.html` | The website |
| `collect_jobs.py` | Reads the company job lists (and Adzuna, if a key is set) and updates `jobs.json` |
| `config.json` | Settings: companies, job-title words, cities |
| `.github/workflows/update-jobs.yml` | Tells GitHub when to collect jobs and publish the site |
| `jobs.json` | Created automatically. The job list the website shows |

## Changing things

- **Refresh now:** open the **Actions** tab, click **Update job board**, then **Run workflow**.
- **Add a company:** open the company's job page. If its address looks like one of these, add a line to `companies` in `config.json`:
  - `boards.greenhouse.io/NAME` or `job-boards.greenhouse.io/NAME`: add `{"name": "Company", "ats": "greenhouse", "slug": "NAME"}`
  - `jobs.lever.co/NAME`: use `"ats": "lever"`
  - `jobs.ashbyhq.com/NAME`: use `"ats": "ashby"`
  - `jobs.smartrecruiters.com/NAME`: use `"ats": "smartrecruiters"`
- **Add a city:** add, for example, `"Mumbai": ["mumbai"]` under `cities`.

To edit `config.json` on GitHub, open it, click the pencil icon, make the change, and click **Commit changes**. The site updates within a couple of minutes.
