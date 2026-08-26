---
name: Data Analysis
description: Clean, analyse and summarise datasets (CSV/JSON/logs) with stdlib or pandas, produce trustworthy numbers, tables and charts. Use for "analyse this data", reports, statistics, log parsing or dataset cleaning tasks.
tags: [data, csv, pandas, statistics, analysis, cleaning, charts]
version: 1.0
agents: ["worker", "coder", "researcher"]
---

# Skill: Data Analysis

## Order of operations
```
LOAD → PROFILE → CLEAN → ANALYSE → VALIDATE → PRESENT
```
Never analyse before profiling. Half of all wrong conclusions come from unexamined data.

## 1. Profile first (always)
```python
import pandas as pd
df = pd.read_csv(path)
print(df.shape)                       # rows, cols
print(df.dtypes)                      # types wrong? dates as object?
print(df.head(3))
print(df.isna().sum())                # missing per column
print(df.describe(include="all").T)   # ranges, outliers, cardinality
print(df.duplicated().sum())
```
Stdlib-only (Termux friendly, no pandas):
```python
import csv, statistics
from collections import Counter
rows = list(csv.DictReader(open(path, encoding="utf-8")))
print(len(rows), rows[0].keys())
col = [float(r["price"]) for r in rows if r["price"].strip()]
print(min(col), max(col), statistics.mean(col), statistics.median(col))
print(Counter(r["category"] for r in rows).most_common(5))
```

## 2. Clean — and log every decision
```python
before = len(df)
df = df.drop_duplicates()
df["date"] = pd.to_datetime(df["date"], errors="coerce")
df["price"] = pd.to_numeric(df["price"].astype(str).str.replace(r"[₹$,]", "", regex=True),
                            errors="coerce")
df = df.dropna(subset=["price", "date"])
print(f"cleaned: {before} -> {len(df)} rows ({before - len(df)} dropped)")
```
Report the drop count. Silently losing 40% of rows is a bug, not a cleaning step.

Missing data: choose deliberately — drop (if rare & random), fill with median (numeric),
fill "unknown" (categorical), or forward-fill (time series). State which and why.

## 3. Analyse
```python
df.groupby("category").agg(n=("price", "size"),
                           mean=("price", "mean"),
                           median=("price", "median")).sort_values("n", ascending=False)

df.set_index("date").resample("W")["price"].sum()          # weekly trend
df["z"] = (df.price - df.price.mean()) / df.price.std()    # outliers |z| > 3
df.corr(numeric_only=True)                                  # relationships
```
Use **median** for skewed data (prices, incomes, latencies); the mean lies there.

## 4. Validate before reporting
```
□ Do totals reconcile with the raw source?
□ Percentages sum to ~100?
□ Any impossible values (negative age, future dates, price=0)?
□ Does the trend survive removing the top 1% outliers?
□ Is n large enough for the claim? (n=7 is an anecdote)
□ Spot-check 3 computed rows by hand
```

## 5. Present
- Lead with the answer, then the evidence.
- Round sensibly (₹1,23,456 not 123456.789012).
- Always give **n** alongside a percentage: "62% (n=1,340)".
- Tables for ≤10 rows of comparison; charts for trends and distributions.
- State the data's date range and source.

```python
import matplotlib
matplotlib.use("Agg")            # headless / Termux: no display
import matplotlib.pyplot as plt
ax = df.groupby("month")["revenue"].sum().plot(kind="bar", figsize=(9, 4))
ax.set_title("Revenue by month"); ax.set_ylabel("₹")
plt.tight_layout(); plt.savefig("revenue.png", dpi=120)
```
Chart rules: label both axes with units, start bar charts at zero, one message per chart,
no 3D, no pie charts with >5 slices.

## Big files on a phone
```python
for chunk in pd.read_csv(path, chunksize=50_000):
    process(chunk)                       # never load 2 GB into RAM
```
Or filter first with shell: `awk -F, '$3>100' big.csv > small.csv`.
`sqlite3` is excellent for multi-GB CSV on Termux: import once, query with SQL.

## Statistical honesty
- Correlation ≠ causation. Say "associated with", not "causes", unless there is an experiment.
- Report the uncertainty: range, IQR, or confidence interval.
- Beware survivorship bias (only successes in the dataset) and selection bias.
- A change is only "significant" if you tested it; otherwise call it "an observed difference".

## Deliverables
`analysis.py` (reproducible, runs top to bottom) · `report.md` (answer + tables + chart refs)
· `charts/*.png` · cleaned `data_clean.csv`. Paste the key numbers into the final answer —
don't just say "see the file".

## Anti-patterns
❌ Analysing before profiling · ❌ mean on skewed data · ❌ percentages without n ·
❌ dropping rows silently · ❌ charts without axis labels ·
❌ conclusions the sample size cannot support · ❌ `df.fillna(0)` on everything
