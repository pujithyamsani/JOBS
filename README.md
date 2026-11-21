# Data Engineer job board

A searchable website of Data Engineer jobs in India that updates itself. GitHub collects new jobs from [Adzuna](https://www.adzuna.in) every 2 hours, from 6:45 AM to 10:45 PM India time, and republishes the site. Your laptop can be off.

Once it's set up, the site is at **https://pujithyamsani.github.io/jobs/**.

## What the site does

- **Search** by job title, company or skill (for example `Databricks`, `Azure`, `Deloitte`). Press `/` to jump to the search box.
- **Filter** by city (Hyderabad, Bengaluru, Pune, Chennai, Gurugram, Noida, Remote, other cities) and by when the job was posted.
- **Seniority:** senior and lead roles are hidden by default, and so are jobs that ask for 4+ years of experience. Turn either switch off to see them.
- **New jobs** since your last visit get a gold **New** label and have their own tab.
- **Save**, **Applied** and **Hide** buttons keep your own lists. They're stored in the browser you use, so your laptop and your phone keep separate lists.
- Jobs stay on the board for 30 days.

## Files

| File | What it does |
|---|---|
| `index.html` | The website |
| `collect_jobs.py` | Collects jobs from Adzuna and updates `jobs.json` |
| `config.json` | Settings: search words, cities, and which titles to leave out |
| `.github/workflows/update-jobs.yml` | Tells GitHub when to collect jobs and publish the site |
| `jobs.json` | Created automatically. The job list the website shows |

## One-time setup (about 10 minutes, free)

1. **Create the repository.** On github.com, click **+** then **New repository**. Name it `jobs`, choose **Public**, tick **Add a README file**, and click **Create repository**.
2. **Turn on the website.** In the repository, go to **Settings**, then **Pages**. Under **Build and deployment**, set **Source** to **GitHub Actions**.
3. **Get a free Adzuna key.** Sign up at <https://developer.adzuna.com/signup>. Your dashboard shows an **Application ID** and an **Application Key**.
4. **Add the key to GitHub.** Go to **Settings**, then **Secrets and variables**, then **Actions**, then **New repository secret**. Add two secrets:
   - `ADZUNA_APP_ID`: your Application ID
   - `ADZUNA_APP_KEY`: your Application Key
5. **Upload the files.** Go to the **Code** tab and click **Add file** then **Upload files**. Drag in everything from the `job-board` folder: `index.html`, `collect_jobs.py`, `config.json`, `README.md` and the whole `.github` folder. Click **Commit changes**.
6. **Wait about 2 minutes.** The upload starts the first update. You can watch it on the **Actions** tab. Then open https://pujithyamsani.github.io/jobs/.

If the repository doesn't show a `.github` folder after the upload, click **Add file** then **Create new file**. Type `.github/workflows/update-jobs.yml` as the name, paste in that file's contents, and commit.

## Updating it yourself

- **Refresh now:** open the **Actions** tab, click **Update job board**, then **Run workflow**.
- **Change what's collected:** open `config.json` on GitHub, click the pencil icon, edit it, and click **Commit changes**. The site updates within a couple of minutes. For example:
  - **Add Kolkata:** add `"Kolkata": ["kolkata"]` under `cities`.
  - **Search more titles:** add words like `"airflow"` to `search_terms` and `title_must_include_any`.

## Good to know

- **Public site:** the website and repository are public because free GitHub Pages needs that. The site doesn't show your name and asks search engines not to list it. Only people with the link will find it.
- **Cost:** free. Each update takes well under a minute.
- **Adzuna usage:** each update makes about 8 searches, so about 72 a day. Your Adzuna account page shows your limits. If you hit one, remove some entries from `search_terms`.
- **Coverage:** Adzuna collects listings from many job sites and company pages, but not every LinkedIn or Naukri posting. Check those sites too.
- **If an update fails:** the site keeps showing the last good list with a note at the top, and it tries again at the next scheduled time.
