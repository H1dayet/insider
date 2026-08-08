"""Shared config. Split out so run.py and render.py can both use it without a circular import."""
import os

# "still cheap" = current price at or below (1 + THRESHOLD) * entry price.
# This is the nominal, informational "vs your entry" number shown on cards -
# it no longer drives the alert gate, see RS_LOW/RS_HIGH below.
THRESHOLD = 0.0

# Alert gate: relative strength (stock return minus its sector benchmark's
# return, both since the disclosed transaction date, in percentage points)
# must fall in [RS_LOW, RS_HIGH] to alert. Tuning knobs, not backtested
# constants - 52 historical buys isn't enough to fit these rigorously.
RS_LOW = -10.0   # hasn't badly underperformed its sector since the buy
RS_HIGH = 2.0    # hasn't already outrun its sector since the buy

# SEC EDGAR requires a descriptive User-Agent identifying the requester
# (https://www.sec.gov/os/webmaster-faq#developers) in their exact documented
# format "Company Name AdminContact@domain.com" - verified live that SEC's
# WAF 403s a parenthetical/no-email UA but accepts this shape. Read from env
# rather than hardcoding a personal contact in source that may end up in a
# public repo.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "insider-bot contact@example.com")
