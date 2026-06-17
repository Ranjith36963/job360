============================================================================================
JOB360 ENGINE ABLATION — STRONG (bootstrap 95% CIs + significance)
============================================================================================
Gold-graded jobs (the Claude benchmark): 100
NDCG = are the BEST jobs at the top (top-heavy; your priority). NDCG@5/@10 = top-5/10 only.
Spearman = is the WHOLE list in order. [lo,hi] = 95% confidence interval (bootstrap).

CONFIG          NDCG [95% CI]         NDCG@5    NDCG@10   Spearman [95% CI]        n
--------------------------------------------------------------------------------------------
E3+E4           0.953 [0.93,0.97]     0.902     0.884     0.778 [0.68,0.84]      100
E3 hybrid(full) 0.951 [0.93,0.97]     0.911     0.891     0.780 [0.67,0.85]      100
E1+E3+E4        0.950 [0.93,0.97]     0.906     0.871     0.770 [0.67,0.84]      100
E2+E3           0.949 [0.92,0.97]     0.884     0.896     0.774 [0.68,0.84]      100
E2+E3+E4        0.949 [0.92,0.97]     0.886     0.880     0.768 [0.67,0.84]      100
E1+E3           0.949 [0.93,0.97]     0.881     0.897     0.771 [0.67,0.84]      100
All (1+2+3+4)   0.946 [0.92,0.96]     0.884     0.840     0.760 [0.66,0.83]      100
E1+E2+E3        0.944 [0.92,0.96]     0.878     0.894     0.758 [0.66,0.83]      100
E1+E2+E4        0.937 [0.89,0.96]     0.816     0.827     0.723 [0.62,0.80]      100
E1+E4           0.934 [0.88,0.96]     0.831     0.845     0.724 [0.62,0.80]      100
E2+E4           0.933 [0.88,0.96]     0.812     0.819     0.723 [0.62,0.80]      100
E4 judge        0.933 [0.85,0.98]     0.761     0.855     0.569 [0.12,0.83]       21
E1+E2           0.932 [0.90,0.96]     0.878     0.808     0.714 [0.61,0.79]      100
E2 dimensions   0.930 [0.89,0.96]     0.830     0.759     0.717 [0.61,0.80]      100
E1 keyword      0.929 [0.89,0.96]     0.896     0.806     0.713 [0.60,0.79]      100
  E3 bm25-only  0.925 [0.87,0.95]     0.861     0.788     0.557 [0.38,0.70]       79
--------------------------------------------------------------------------------------------

SIGNIFICANCE — is the top config (E3+E4) really better on NDCG, or noise?
(paired bootstrap of the NDCG difference; SIGNIFICANT = 95% CI of the gap excludes 0)

  E3+E4 vs E3 hybrid(full)  ΔNDCG=+0.002 CI=[-0.013,0.016]     not significant (overlaps)
  E3+E4 vs E1+E3+E4         ΔNDCG=+0.004 CI=[-0.000,0.010]     not significant (overlaps)
  E3+E4 vs E2+E3            ΔNDCG=+0.004 CI=[-0.004,0.014]     not significant (overlaps)
  E3+E4 vs E2+E3+E4         ΔNDCG=+0.004 CI=[0.000,0.012]      SIGNIFICANT
  E3+E4 vs E1+E3            ΔNDCG=+0.004 CI=[-0.003,0.014]     not significant (overlaps)
  E3+E4 vs All (1+2+3+4)    ΔNDCG=+0.007 CI=[0.002,0.017]      SIGNIFICANT
  E3+E4 vs E1+E2+E3         ΔNDCG=+0.009 CI=[0.002,0.021]      SIGNIFICANT
  E3+E4 vs E1+E2+E4         ΔNDCG=+0.017 CI=[0.003,0.047]      SIGNIFICANT
  E3+E4 vs E1+E4            ΔNDCG=+0.019 CI=[0.001,0.059]      SIGNIFICANT
  E3+E4 vs E2+E4            ΔNDCG=+0.020 CI=[0.004,0.062]      SIGNIFICANT
  E3+E4 vs E4 judge         ΔNDCG=+0.034 CI=[-0.016,0.123]     not significant (overlaps)
  E3+E4 vs E1+E2            ΔNDCG=+0.021 CI=[0.008,0.043]      SIGNIFICANT
  E3+E4 vs E2 dimensions    ΔNDCG=+0.023 CI=[0.008,0.049]      SIGNIFICANT
  E3+E4 vs E1 keyword       ΔNDCG=+0.024 CI=[0.008,0.049]      SIGNIFICANT
  E3+E4 vs   E3 bm25-only   ΔNDCG=+0.028 CI=[0.002,0.071]      SIGNIFICANT

============================================================================================
TOP-5 JOBS PER CONFIG (job_id · gold-fit · title) — the 'why'
============================================================================================

[E3+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    70  #9  Internship Core Modeling AI/ML Software Engineer
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    58  #5  ML/AI Engineer Intern

[E3 hybrid(full)]
    72  #1270  Intern, AI Engineer
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E1+E3+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E2+E3]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    58  #5  ML/AI Engineer Intern
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E2+E3+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    70  #9  Internship Core Modeling AI/ML Software Engineer
    58  #5  ML/AI Engineer Intern
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E1+E3]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    58  #5  ML/AI Engineer Intern

[All (1+2+3+4)]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    58  #5  ML/AI Engineer Intern
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E1+E2+E3]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    58  #5  ML/AI Engineer Intern
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G

[E1+E2+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    30  #26  Remote AI / ML Engineer
    58  #5  ML/AI Engineer Intern

[E1+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    30  #26  Remote AI / ML Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026
    70  #9  Internship Core Modeling AI/ML Software Engineer

[E2+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    30  #26  Remote AI / ML Engineer
    58  #5  ML/AI Engineer Intern
    70  #9  Internship Core Modeling AI/ML Software Engineer

[E4 judge]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    30  #26  Remote AI / ML Engineer
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    42  #1  Senior Specialist Solutions Engineer (AI/ML)

[E1+E2]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    58  #5  ML/AI Engineer Intern
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[E2 dimensions]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    58  #5  ML/AI Engineer Intern
    48  #12  ML Engineer Apprentice Build AI; Remote
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E1 keyword]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    62  #8  PhD Intern, AI/ML/NLP Engineer
    70  #9  Internship Core Modeling AI/ML Software Engineer
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[  E3 bm25-only]
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #11  Internship: AI/ML for Ionospheric TEC Modelling
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    80  #7  Internship @ Sqwish (AI Research / Backend / ML Engineering)
    28  #2  Member of Technical Staff (AI Inference Engineer)
============================================================================================
