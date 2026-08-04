-- 024_rule_governance.sql
-- Preserve the named actor and reason for every analyst-rule state change.

BEGIN;

ALTER TABLE wnba.analyst_rules
    ADD COLUMN IF NOT EXISTS approval_reason TEXT,
    ADD COLUMN IF NOT EXISTS retired_by TEXT,
    ADD COLUMN IF NOT EXISTS retirement_reason TEXT;

ALTER TABLE wnba.analyst_rules
    DROP CONSTRAINT IF EXISTS active_rules_require_an_approval_reason;
ALTER TABLE wnba.analyst_rules
    ADD CONSTRAINT active_rules_require_an_approval_reason CHECK (
        status <> 'active' OR length(trim(coalesce(approval_reason, ''))) > 0);

ALTER TABLE wnba.analyst_rules
    DROP CONSTRAINT IF EXISTS retired_rules_require_a_human_and_reason;
ALTER TABLE wnba.analyst_rules
    ADD CONSTRAINT retired_rules_require_a_human_and_reason CHECK (
        status <> 'retired' OR (
            retired_at IS NOT NULL
            AND length(trim(coalesce(retired_by, ''))) > 0
            AND length(trim(coalesce(retirement_reason, ''))) > 0));

COMMIT;
