-- Transcripts: tipado. Son plantillas (2 aperturas + 4 cierres) sin relación con el motivo real;
-- main_topics replica la categoría del contacto (fuga si se usa como feature).
SELECT
    transcript_id, interaction_id,
    TRY_CAST(process_date AS DATE)           AS process_date,
    customer_id, agent_id,
    full_text, customer_text, agent_text,
    detected_language, detected_accent,
    TRY_CAST(accent_confidence AS DOUBLE)    AS accent_confidence,
    detected_keywords, mentioned_entities, detected_intents, main_topics,
    transcription_model, audio_quality,
    TRY_CAST(duration_seconds AS DOUBLE)     AS duration_s,
    agent_text LIKE '%{%'                    AS dq_agent_text_placeholder,  -- "{monto} {moneda}" sin llenar
    regexp_extract(_source_file, '([^/]+)$', 1) AS _source_file,
    _ingested_at
FROM bronze.call_transcripts
QUALIFY ROW_NUMBER() OVER (PARTITION BY transcript_id ORDER BY _ingested_at DESC, _source_file DESC) = 1
