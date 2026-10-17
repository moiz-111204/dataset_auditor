"""Generate the bundled sample datasets.

ALL DATA PRODUCED BY THIS SCRIPT IS SYNTHETIC. It is not about real people. Names are random
combinations from short lists, emails use the reserved example.com domain, phone numbers use
the fictional 555-01xx range, and SSN-style IDs start with 000, 666 or 9xx, which are never
issued, so none of them can belong to anyone.

Run from the project root:  python sample_data/generate_samples.py
Output is deterministic (fixed random seed).

problematic_hiring.csv plants these issues on purpose:
- Gender gap: the brief asked for roughly 40% vs 70% hiring rates and also about 25% hired
  overall. Both cannot hold at once, so the generator keeps the 40:70 ratio (women are hired
  at about 57% of the men's rate) and the ~25% overall rate (~23% here, a >3:1 imbalance).
- Lower hiring rates for minority race groups and for applicants over 50.
- zip_code strongly tied to race (a proxy).
- income missing for ~35% of rows, much more often for women; education missing for ~15%.
- Direct identifiers: names, emails, phones and SSN-style IDs.

clean_survey.csv has no PII, no protected attributes, balanced classes and under 2% missing.
"""

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
OUT_DIR = Path(__file__).resolve().parent

FEMALE_NAMES = ["Amara", "Bianca", "Chloe", "Dana", "Elena", "Fatima", "Grace", "Hana", "Imani",
                "Jasmine", "Keiko", "Lena", "Maya", "Nadia", "Olivia", "Priya", "Rosa", "Sofia",
                "Tara", "Uma", "Valeria", "Wen", "Yara", "Zoe"]
MALE_NAMES = ["Aarav", "Ben", "Carlos", "Daniel", "Ethan", "Farid", "Gabriel", "Hiro", "Ivan",
              "Jamal", "Kofi", "Liam", "Mateo", "Noah", "Omar", "Pedro", "Quentin", "Rahul",
              "Samuel", "Tomas", "Victor", "Wei", "Yusuf", "Zane"]
NEUTRAL_NAMES = ["Alex", "Jordan", "Riley", "Sam", "Taylor"]
LAST_NAMES = ["Adeyemi", "Brooks", "Chen", "Diaz", "Evans", "Fischer", "Garcia", "Haddad", "Ito",
              "Johnson", "Kim", "Lopez", "Mensah", "Nguyen", "Okafor", "Patel", "Quinn", "Rossi",
              "Singh", "Tanaka", "Usman", "Valdez", "Walker", "Xu", "Young", "Zhang"]
AREA_CODES = ["212", "312", "415", "617", "713", "206", "305", "404"]

RACES = ["White", "Black", "Hispanic", "Asian"]
RACE_SHARES = [0.5, 0.2, 0.2, 0.1]
RACE_HIRE_FACTOR = {"White": 1.0, "Black": 0.55, "Hispanic": 0.6, "Asian": 0.9}
RACE_ZIPS = {"White": [10001, 10002, 10003, 10004], "Black": [20001, 20002, 20003],
             "Hispanic": [30001, 30002, 30003], "Asian": [40001, 40002]}
ALL_ZIPS = [z for zips in RACE_ZIPS.values() for z in zips]


def fake_ssn(rng: np.random.Generator) -> str:
    area = rng.choice(["000", "666", f"9{rng.integers(0, 100):02d}"])
    return f"{area}-{rng.integers(1, 100):02d}-{rng.integers(1, 10000):04d}"


def problematic_hiring(rng: np.random.Generator, n: int = 500) -> pd.DataFrame:
    gender = rng.choice(["Female", "Male", "Non-binary"], size=n, p=[0.49, 0.49, 0.02])
    race = rng.choice(RACES, size=n, p=RACE_SHARES)
    age = rng.integers(22, 64, size=n)
    zip_code = [int(rng.choice(RACE_ZIPS[r])) if rng.random() < 0.88 else int(rng.choice(ALL_ZIPS)) for r in race]
    education = rng.choice(["High school", "Bachelor's", "Master's", "PhD"], size=n, p=[0.25, 0.45, 0.22, 0.08])
    experience = np.clip(age - 22 - rng.integers(0, 6, size=n), 0, None)
    score = np.clip(rng.normal(70, 12, size=n), 30, 100).round(1)

    gender_factor = np.where(gender == "Male", 1.0, np.where(gender == "Female", 0.42, 0.8))
    race_factor = np.array([RACE_HIRE_FACTOR[r] for r in race])
    age_factor = np.where(age > 50, 0.45, 1.0)
    score_factor = 0.55 + 0.9 * (score - 30) / 70
    p_hire = np.clip(0.42 * gender_factor * race_factor * age_factor * score_factor, 0, 0.95)
    hired = (rng.random(n) < p_hire).astype(int)

    income = (rng.normal(48000, 12000, size=n) + experience * 900).round(-2)
    missing_income = rng.random(n) < np.where(gender == "Female", 0.49, 0.19)
    income = np.where(missing_income, np.nan, income)
    # Education gaps are random but spread evenly: exactly ~15% within each gender group,
    # so they do not create an accidental group difference.
    education = education.astype(object)
    for g in np.unique(gender):
        idx = np.flatnonzero(gender == g)
        education[rng.choice(idx, size=round(0.15 * len(idx)), replace=False)] = None

    first = [rng.choice(FEMALE_NAMES if g == "Female" else MALE_NAMES if g == "Male" else NEUTRAL_NAMES)
             for g in gender]
    last = [rng.choice(LAST_NAMES) for _ in range(n)]
    email = [f"{f.lower()}.{l.lower()}{i}@example.com" for i, (f, l) in enumerate(zip(first, last))]
    phone = [f"({rng.choice(AREA_CODES)}) 555-01{rng.integers(0, 100):02d}" for _ in range(n)]

    return pd.DataFrame({
        "applicant_id": [f"A{i:04d}" for i in range(1, n + 1)],
        "first_name": first,
        "last_name": last,
        "email": email,
        "phone": phone,
        "ssn": [fake_ssn(rng) for _ in range(n)],
        "gender": gender,
        "race": race,
        "age": age,
        "zip_code": zip_code,
        "education": education,
        "years_experience": experience,
        "interview_score": score,
        "income": income,
        "hired": hired,
    })


def clean_survey(rng: np.random.Generator, n: int = 300) -> pd.DataFrame:
    frequency = np.array(["Weekly", "Monthly", "Quarterly", "Rarely"] * (n // 4))
    rng.shuffle(frequency)
    satisfaction = rng.integers(1, 11, size=n).astype(float)
    satisfaction[rng.choice(n, size=3, replace=False)] = np.nan  # 1% missing
    return pd.DataFrame({
        "response_id": [f"R{i:04d}" for i in range(1, n + 1)],
        "region": rng.choice(["North", "South", "East", "West"], size=n),
        "product_category": rng.choice(["Electronics", "Home & Garden", "Books", "Clothing", "Sports"], size=n),
        "satisfaction_score": satisfaction,
        "would_recommend": rng.choice(["Yes", "No"], size=n, p=[0.55, 0.45]),
        "purchase_frequency": frequency,
    })


def main() -> None:
    rng = np.random.default_rng(SEED)
    hiring = problematic_hiring(rng)
    survey = clean_survey(rng)
    # Explicit "\n" so the files are byte-identical on Windows, macOS and Linux.
    hiring.to_csv(OUT_DIR / "problematic_hiring.csv", index=False, lineterminator="\n")
    survey.to_csv(OUT_DIR / "clean_survey.csv", index=False, lineterminator="\n")
    print(f"problematic_hiring.csv: {len(hiring)} rows, hired share {hiring['hired'].mean():.1%}")
    for g, rate in hiring.groupby("gender")["hired"].mean().items():
        print(f"  hired rate {g}: {rate:.1%}")
    print(f"  income missing: {hiring['income'].isna().mean():.1%}, education missing: {hiring['education'].isna().mean():.1%}")
    print(f"clean_survey.csv: {len(survey)} rows, missing cells {survey.isna().to_numpy().mean():.2%}")


if __name__ == "__main__":
    main()
