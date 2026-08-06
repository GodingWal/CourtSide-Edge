-- 030_line_bias_fitted_kind.sql
-- Let fitted_parameters carry the measured line-band bias fit.
--
-- The fitted-parameter store was created with a CHECK over the three kinds it knew about.
-- The line-band bias fit (PR #66) is a fourth kind, and the first live fitting run after
-- that merge correctly refused to store it. Widening the check is the whole change; the
-- row shape, the supersede-not-update discipline, and the payload contract are unchanged.

BEGIN;

ALTER TABLE wnba.fitted_parameters
    DROP CONSTRAINT fitted_parameters_kind_check;
ALTER TABLE wnba.fitted_parameters
    ADD CONSTRAINT fitted_parameters_kind_check CHECK (
        kind = ANY (
            ARRAY[
                'calibration_map'::text,
                'ensemble_weights'::text,
                'edge_shrinkage'::text,
                'line_bias'::text
            ]
        )
    );

COMMIT;
