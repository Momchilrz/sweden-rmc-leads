# MR Project

A local staging desk for public construction notices. The source code is open source under the MIT License. See [LICENSE](LICENSE).

The desk does not connect to Dynamics 365 or Dataverse. It stores a local copy so the records can be checked before any later CRM load.

## What the license covers

The MIT License applies to the source code in this repository, including the Sweden, United Kingdom, Belgium, and Netherlands loaders. Colleagues may use, change, and share that code, including inside other projects, as long as they keep the copyright notice and license text.

The license does not cover the notice data. Trafikverket, TED, OpenTender, Find a Tender, Contracts Finder, and TenderNed each keep their own terms. OpenTender publishes its downloads under CC BY-NC-SA 4.0, which is limited to non-commercial use. This repository does not relicense that data.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

Trafikverket needs `TRAFIKVERKET_API_KEY` in the environment or in Streamlit secrets. TED and OpenTender do not.
