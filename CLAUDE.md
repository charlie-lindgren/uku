# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

UKU (Utskottet för Kurs- och Utbildningsplaner) is a working repository for the course and curriculum plan evaluation committee at IIT (Institutionen för Information och Teknologi), Högskolan Dalarna. The committee handles continuous improvement and quality control of course plans and curriculum plans.

The repository is **not a software project** — it contains documents, notes, and data related to ongoing quality assurance work.

## Repository structure

```text
vault-uku/             Obsidian vault — living knowledge base
  00 Dashboard/        Overview and navigation hub
  01 Kvalitet/         Quality processes and guidelines
  02 Kursplaner/       Course plans organized by subject area
    DT1/               Datateknik course plans
    IF1/               Informatik course plans
  03 Möten/            Meeting log
  04 Organisation/     Committee composition
  Templates/           Obsidian templates
content -> vault-uku   Symlink for Quartz site build
quartz/                Quartz 4 static site engine
quartz.config.ts       Site configuration
quartz.layout.ts       Page layout configuration
```

## Quartz site

The vault is published as a Quartz 4 static site. Build with:

```bash
npm ci
npx quartz build        # build to public/
npx quartz build --serve  # local preview at localhost:8080
```

Deploy is automatic via GitHub Actions on push to main.

## Course plan webscraping

Course plans can be scraped from du.se using the scraper script. Additional subject areas beyond DT1 and IF1 may be added over time.
