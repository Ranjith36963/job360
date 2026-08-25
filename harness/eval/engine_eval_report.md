<!-- doc: LOG -->
============================================================================================
JOB360 ENGINE ABLATION — STRONG (bootstrap 95% CIs + significance)
============================================================================================
Gold-graded jobs (the Claude benchmark): 100
NDCG = best jobs at the top (your priority). @3 = top-3 only. exp = exponential-gain
(rewards the #1 spot hardest — sharpest 'best job at top'). Spearman = whole-list order.
[lo,hi] = 95% bootstrap confidence interval.

CONFIG          NDCG [95% CI]         @3      @5      exp     Spearman [95% CI]        n
------------------------------------------------------------------------------------------------
E2+E3+E4        0.962 [0.94,0.98]     0.911   0.904   0.918   0.880 [0.83,0.91]      100
E2+E3           0.960 [0.94,0.98]     0.863   0.879   0.916   0.878 [0.82,0.91]      100
E1+E3+E4        0.958 [0.93,0.98]     0.911   0.900   0.915   0.873 [0.81,0.91]      100
All (1+2+3+4)   0.958 [0.93,0.97]     0.863   0.899   0.912   0.873 [0.82,0.91]      100
E3 hybrid(full) 0.957 [0.93,0.98]     0.906   0.911   0.914   0.831 [0.74,0.89]      100
E1+E2+E3        0.953 [0.93,0.97]     0.875   0.878   0.907   0.861 [0.79,0.90]      100
E1+E3           0.953 [0.93,0.97]     0.875   0.878   0.908   0.836 [0.75,0.89]      100
E1+E2+E4        0.951 [0.91,0.97]     0.863   0.817   0.900   0.863 [0.79,0.90]      100
E3+E4           0.949 [0.91,0.97]     0.824   0.831   0.896   0.876 [0.82,0.91]      100
E2+E4           0.944 [0.90,0.97]     0.804   0.837   0.887   0.849 [0.79,0.89]      100
E2 dimensions   0.944 [0.91,0.97]     0.863   0.830   0.891   0.853 [0.78,0.90]      100
E1+E2           0.943 [0.91,0.96]     0.875   0.878   0.890   0.843 [0.77,0.90]      100
E1+E4           0.937 [0.88,0.97]     0.780   0.770   0.881   0.850 [0.78,0.90]      100
E1 keyword      0.933 [0.89,0.96]     0.869   0.896   0.876   0.797 [0.70,0.86]      100
E4 judge        0.915 [0.87,0.95]     0.749   0.746   0.833   0.822 [0.74,0.88]       99
  E3 bm25-only  0.906 [0.84,0.94]     0.777   0.740   0.848   0.668 [0.52,0.78]      100
------------------------------------------------------------------------------------------------

SIGNIFICANCE — is the top config (E2+E3+E4) really better on NDCG, or noise?
(paired bootstrap of the NDCG difference; SIGNIFICANT = 95% CI of the gap excludes 0)

  E2+E3+E4 vs E2+E3            ΔNDCG=+0.002 CI=[-0.012,0.017]     not significant (overlaps)
  E2+E3+E4 vs E1+E3+E4         ΔNDCG=+0.004 CI=[-0.004,0.015]     not significant (overlaps)
  E2+E3+E4 vs All (1+2+3+4)    ΔNDCG=+0.005 CI=[-0.004,0.017]     not significant (overlaps)
  E2+E3+E4 vs E3 hybrid(full)  ΔNDCG=+0.006 CI=[-0.011,0.026]     not significant (overlaps)
  E2+E3+E4 vs E1+E2+E3         ΔNDCG=+0.009 CI=[-0.004,0.026]     not significant (overlaps)
  E2+E3+E4 vs E1+E3            ΔNDCG=+0.010 CI=[-0.006,0.029]     not significant (overlaps)
  E2+E3+E4 vs E1+E2+E4         ΔNDCG=+0.012 CI=[-0.001,0.038]     not significant (overlaps)
  E2+E3+E4 vs E3+E4            ΔNDCG=+0.013 CI=[-0.001,0.046]     not significant (overlaps)
  E2+E3+E4 vs E2+E4            ΔNDCG=+0.018 CI=[0.001,0.056]      SIGNIFICANT
  E2+E3+E4 vs E2 dimensions    ΔNDCG=+0.018 CI=[-0.001,0.047]     not significant (overlaps)
  E2+E3+E4 vs E1+E2            ΔNDCG=+0.020 CI=[0.004,0.045]      SIGNIFICANT
  E2+E3+E4 vs E1+E4            ΔNDCG=+0.025 CI=[0.003,0.073]      SIGNIFICANT
  E2+E3+E4 vs E1 keyword       ΔNDCG=+0.030 CI=[0.009,0.062]      SIGNIFICANT
  E2+E3+E4 vs E4 judge         ΔNDCG=+0.048 CI=[0.017,0.090]      SIGNIFICANT
  E2+E3+E4 vs   E3 bm25-only   ΔNDCG=+0.056 CI=[0.018,0.122]      SIGNIFICANT

============================================================================================
TOP-5 JOBS PER CONFIG (job_id · gold-fit · title) — the 'why'
============================================================================================

[E2+E3+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    58  #5  ML/AI Engineer Intern
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[E2+E3]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    58  #5  ML/AI Engineer Intern
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E1+E3+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    58  #5  ML/AI Engineer Intern

[All (1+2+3+4)]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    58  #5  ML/AI Engineer Intern
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[E3 hybrid(full)]
    72  #1270  Intern, AI Engineer
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E1+E2+E3]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    58  #5  ML/AI Engineer Intern
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G

[E1+E3]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    58  #5  ML/AI Engineer Intern
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G

[E1+E2+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    58  #5  ML/AI Engineer Intern
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    30  #26  Remote AI / ML Engineer

[E3+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G
    40  #42  AI Architect
    62  #78  AI Innovation Engineer / AI Solutions Engineer
    64  #1275  Machine Learning Engineer Intern (100% Remote Worldwide)

[E2+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #2316  Graduate AI Engineer
    38  #179  AI ML Associate
    64  #1275  Machine Learning Engineer Intern (100% Remote Worldwide)
    74  #19  AI/ML Software Engineer Internship – 12 Months, 5G

[E2 dimensions]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    58  #5  ML/AI Engineer Intern
    48  #12  ML Engineer Apprentice Build AI; Remote
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E1+E2]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    58  #5  ML/AI Engineer Intern
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[E1+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    30  #26  Remote AI / ML Engineer
    38  #179  AI ML Associate
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[E1 keyword]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    62  #8  PhD Intern, AI/ML/NLP Engineer
    70  #9  Internship Core Modeling AI/ML Software Engineer
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[E4 judge]
    70  #2316  Graduate AI Engineer
    38  #179  AI ML Associate
    64  #1275  Machine Learning Engineer Intern (100% Remote Worldwide)
    48  #678  AI Engineer - FDE (Forward Deployed Engineer)
    62  #78  AI Innovation Engineer / AI Solutions Engineer

[  E3 bm25-only]
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #11  Internship: AI/ML for Ionospheric TEC Modelling
    28  #2  Member of Technical Staff (AI Inference Engineer)
    70  #25  AI - Machine Learning Intern (Gen AI - Multimodal)
    22  #1111  Senior Software Engineer, Backend/Infra
============================================================================================
