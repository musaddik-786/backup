-- Switch severity to osTicket's priority names: Low / Normal / High / Emergency.
-- NOT yet applied to any database. Run once per environment, inside a transaction.
BEGIN;
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['ticket_records','chat_rca_records','release_note_records','runbook_records'] LOOP
    EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I', t, t || '_severity_check');
    EXECUTE format($f$UPDATE %I SET severity = CASE severity
        WHEN 'SEV1-Critical' THEN 'Emergency' WHEN 'SEV2-High' THEN 'High'
        WHEN 'SEV3-Medium' THEN 'Normal' WHEN 'SEV4-Low' THEN 'Low' ELSE severity END$f$, t);
    EXECUTE format($f$ALTER TABLE %I ADD CONSTRAINT %I CHECK (severity IN ('Low','Normal','High','Emergency'))$f$, t, t || '_severity_check');
  END LOOP;
END $$;
COMMIT;
