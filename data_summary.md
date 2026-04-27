# Data Preparation — Summary

## Kaggle (Brazilian no-show data, demand engine)
- Rows: 110,526 | Unique patients: 62,298
- Date range: 2016-04-29 to 2016-06-08
- **No-show rate: 20.2%** (within the 15-30% range reported for U.S. outpatient clinics)
- Median lead time: 4 days; p95: 39 days
- SMS reminder coverage: 32.1%

## NAMCS 2019 (U.S. service-time calibration)
- Rows: 8,250
- `TIMEMD` (minutes with physician): median 20.0, mean 22.2
- Specialty categories available: 3

## MEPS HC-243 (U.S. payer mix, calendar year 2022)
- Rows: 22,431
- **Payer mix used to assign insurance networks:**
  - Any private insurance: 58.1%
  - Public only (Medicare/Medicaid/etc.): 34.7%
  - Uninsured: 7.2%
- Had any outpatient visit in 2022: 73.8%
- Mean outpatient visits / person: 6.74; of which physician visits: 3.06

## CMS Doctors & Clinicians (U.S. provider pool)
- After filtering to outpatient-relevant specialties: 786,227 unique clinicians
- Unique groups (practices): 44,392
- Unique states: 56
- Top 5 specialties:
  - NURSE PRACTITIONER: 256,394
  - PHYSICIAN ASSISTANT: 126,480
  - INTERNAL MEDICINE: 99,829
  - FAMILY PRACTICE: 93,076
  - OBSTETRICS/GYNECOLOGY: 31,351
