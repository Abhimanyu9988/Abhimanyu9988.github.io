---
title: "0% for 70 Seconds After the Dependency Recovered: A Retry Storm in Splunk"
description: "Placeholder while the run's evidence is collected; the article is written from it next."
image: /assets/posts/0-for-70-seconds-after-the-dependency-recovered-a-retry/cover.png
image_alt: "Terminal output comparing naive retries, which stay at 0% success for 70 seconds after the slowdown, with budgeted retries, which recover in 19 seconds."
tags: ["kubernetes", "opentelemetry", "splunk", "aws"]
channels: []
lab: labs/0-for-70-seconds-after-the-dependency-recovered-a-retry
linkedin: "Placeholder.\n{LINKS}\n#Splunk"
replies: []
---
![Terminal output comparing naive retries, which stay at 0% success for 70 seconds after the slowdown, with budgeted retries, which recover in 19 seconds.](https://abhimanyu9988.github.io/assets/posts/0-for-70-seconds-after-the-dependency-recovered-a-retry/cover.png)

Placeholder while the evidence files are collected from the run. The full article replaces this.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/README.md)
- [`analyze.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/analyze.sh)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/k8s/20-load.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/reproduce.sh)
- [`splunk.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/splunk.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/0-for-70-seconds-after-the-dependency-recovered-a-retry/evidence): the raw output of my run
