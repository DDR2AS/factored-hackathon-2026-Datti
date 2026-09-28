# Architecture

Current target architecture from plan v2 (sections 4, 8 and 9 of `docs/plan/expediente-vivo-v2.html`). Kept as Mermaid so it stays reviewable as text; update it when an interface in `INTERFACES.md` changes.

## Customer flow

```mermaid
flowchart TD
    A[1 Customer writes, ES/PT] --> B[2 Understand request]
    B --> C[3 Extract details]
    C --> D[4 Find the charge]
    D --> E[5 Verify and check]
    E --> F{6 Lane rules YAML}
    F -->|A| RA[Resolve now with data]
    F -->|B| G[7 Case opens, promised date, SLA timers]
    F -->|C| RC[Human now, structured handoff]
    G --> H[8 Analyst decides]
    M2[M2 calibrated classifier] -. p class .-> B
    G1[G1 extraction, Haiku 4.5] -. JSON slots .-> C
    M1[M1 transaction ranker] -. top-3 + p .-> D
    M3[M3 risk evidence] -. features .-> E
    G -. money dispute .-> G2[G2 investigator, Sonnet 5]
    G2 -. cited report .-> H
    H -. labels .-> L[Learning loop: retrain M1, M2]
```

## Local development (everyone)

```mermaid
flowchart LR
    S3O[(Organizer S3 bucket)] -->|make data| RAW[data/raw CSV]
    RAW --> DDB[(data/processed/latam_bank.duckdb)]
    DDB -->|make pipeline| GOLD[(data/gold Parquet)]
    GOLD --> GW[src/gateway, duckdb backend]
    GOLD -->|make train| MOD[models/]
    GW --> ORCH[Orchestrator + lane rules]
    MOD --> ORCH
    ORCH --> LLM[src/llm, mock provider]
    ORCH --> API[Local API + front end, make up]
    ORCH --> TR[data/traces JSONL]
```

## Cloud (operated by andres only)

```mermaid
flowchart TD
    WEB[Web app: S3 + CloudFront] -->|/api| APIGW[API Gateway HTTP API]
    COG[Cognito, 15-min sessions] -->|JWT| APIGW
    APIGW -->|invoke| CHAT[Chat Lambda: rules + M1 M2 M3]
    CHAT -->|query| DYN[(DynamoDB: sessions, cases, txns)]
    CHAT -->|JSON| HAIKU[Bedrock Haiku 4.5]
    CHAT -->|open case| SFN[Step Functions case lifecycle]
    AN[Analyst console] <-->|task token| SFN
    EVB[EventBridge Scheduler: SLA timers] -->|fires| SFN
    SFN -->|investigate| INV[Investigator Lambda: gateway tools]
    INV -->|tools| SONNET[Bedrock Sonnet 5]
    INV -->|trace| TRC[(S3 + Athena)]
    RAWC[(S3 raw)] --> PIPE[Lambda + DuckDB pipeline] --> LAKE[(S3 Parquet lake + Glue)] --> ATH[Athena gold] -->|import| DYN
    LAKE --> SM[SageMaker training job] --> REG[(Model registry in S3)] --> CHAT
```
