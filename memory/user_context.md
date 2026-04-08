# User Context

Project context, preferences, team conventions, and test account registry.

---

## Product Context

**Product**: MakeMyTrip (MMT) — India's leading travel booking platform
**Platform**: Android (primary), iOS (Phase 3)
**Team**: Product managers and QA engineers

## Key Product Areas

- Hotel detail and gallery
- Hotel checkout and payment (coupon selector, GST input)
- Thank you / confirmation page
- Login / signup
- Add traveler / passenger
- Home feed and recommendations
- Search results

## A/B Testing Environment

MMT runs continuous A/B experiments. Accounts can be enrolled in different variants.

### Account Registry

| Account ID | Type | Notes |
|------------|------|-------|
| account_1  | returning_user | Default test account |
| account_2  | new_user | Fresh account, no bookings |
| account_3  | premium_user | MyBiz / premium enrolled |

Update this table after each run with observed variant fingerprints.

## Conventions

- Evidence stored in `.tmp/evidence/`
- Reports stored in `reports/`
- Build files stored in `.tmp/builds/`
- Always test across minimum 2 accounts per feature
- Label findings: REGRESSION / BUILD_CHANGE / VARIANT_DIFFERENCE / PASS
