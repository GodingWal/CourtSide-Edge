-- Every attribution keeps its scored cause list and the chain back to an engineering action.
ALTER TABLE wnba.error_attributions
    ADD COLUMN IF NOT EXISTS causal_chain jsonb NOT NULL DEFAULT '[]';
