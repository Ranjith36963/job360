============================================================================================
JOB360 ENGINE ABLATION — STRONG (bootstrap 95% CIs + significance)
============================================================================================
Gold-graded jobs (the Claude benchmark): 45
NDCG = are the BEST jobs at the top (top-heavy; your priority). NDCG@5/@10 = top-5/10 only.
Spearman = is the WHOLE list in order. [lo,hi] = 95% confidence interval (bootstrap).

CONFIG          NDCG [95% CI]         NDCG@5    NDCG@10   Spearman [95% CI]        n
--------------------------------------------------------------------------------------------
E3 hybrid(full) 0.956 [0.92,0.98]     0.920     0.896     0.815 [0.64,0.90]       45
E1+E3           0.953 [0.92,0.97]     0.872     0.868     0.798 [0.62,0.89]       45
E2+E3           0.951 [0.92,0.97]     0.867     0.856     0.810 [0.63,0.90]       45
E3+E4           0.951 [0.92,0.97]     0.920     0.874     0.781 [0.59,0.88]       45
E1+E2+E3        0.949 [0.91,0.97]     0.872     0.865     0.784 [0.61,0.88]       45
E1+E3+E4        0.945 [0.90,0.97]     0.872     0.865     0.772 [0.57,0.87]       45
All (1+2+3+4)   0.943 [0.90,0.97]     0.872     0.862     0.767 [0.58,0.87]       45
  E3 bm25-only  0.934 [0.87,0.97]     0.878     0.813     0.564 [0.28,0.76]       38
E1+E2+E4        0.929 [0.85,0.97]     0.825     0.799     0.716 [0.51,0.84]       45
E2 dimensions   0.928 [0.86,0.96]     0.862     0.755     0.723 [0.53,0.85]       45
E1 keyword      0.924 [0.86,0.96]     0.836     0.756     0.688 [0.47,0.82]       45
E4 judge        0.922 [0.82,0.99]     0.775     0.887     0.604 [0.02,0.93]       15
E1+E4           0.920 [0.84,0.97]     0.769     0.784     0.719 [0.52,0.84]       45
--------------------------------------------------------------------------------------------

SIGNIFICANCE — is the top config (E3 hybrid(full)) really better on NDCG, or noise?
(paired bootstrap of the NDCG difference; SIGNIFICANT = 95% CI of the gap excludes 0)

  E3 hybrid(full) vs E1+E3            ΔNDCG=+0.004 CI=[-0.013,0.024]     not significant (overlaps)
  E3 hybrid(full) vs E2+E3            ΔNDCG=+0.005 CI=[-0.014,0.034]     not significant (overlaps)
  E3 hybrid(full) vs E3+E4            ΔNDCG=+0.005 CI=[-0.016,0.032]     not significant (overlaps)
  E3 hybrid(full) vs E1+E2+E3         ΔNDCG=+0.007 CI=[-0.011,0.033]     not significant (overlaps)
  E3 hybrid(full) vs E1+E3+E4         ΔNDCG=+0.011 CI=[-0.009,0.044]     not significant (overlaps)
  E3 hybrid(full) vs All (1+2+3+4)    ΔNDCG=+0.013 CI=[-0.009,0.052]     not significant (overlaps)
  E3 hybrid(full) vs   E3 bm25-only   ΔNDCG=+0.022 CI=[-0.011,0.076]     not significant (overlaps)
  E3 hybrid(full) vs E1+E2+E4         ΔNDCG=+0.027 CI=[-0.006,0.098]     not significant (overlaps)
  E3 hybrid(full) vs E2 dimensions    ΔNDCG=+0.029 CI=[-0.001,0.087]     not significant (overlaps)
  E3 hybrid(full) vs E1 keyword       ΔNDCG=+0.032 CI=[0.003,0.091]      SIGNIFICANT
  E3 hybrid(full) vs E4 judge         ΔNDCG=+0.043 CI=[-0.036,0.145]     not significant (overlaps)
  E3 hybrid(full) vs E1+E4            ΔNDCG=+0.036 CI=[-0.007,0.109]     not significant (overlaps)

============================================================================================
TOP-5 JOBS PER CONFIG (job_id · gold-fit · title) — the 'why'
============================================================================================

[E3 hybrid(full)]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #3464  AI / ML Intern, Machine Learning (2026)
    62  #8  PhD Intern, AI/ML/NLP Engineer
    74  #11  Internship: AI/ML for Ionospheric TEC Modelling

[E1+E3]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026
    48  #12  ML Engineer Apprentice Build AI; Remote

[E2+E3]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    48  #12  ML Engineer Apprentice Build AI; Remote
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[E3+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026
    62  #8  PhD Intern, AI/ML/NLP Engineer
    74  #11  Internship: AI/ML for Ionospheric TEC Modelling

[E1+E2+E3]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026
    48  #12  ML Engineer Apprentice Build AI; Remote

[E1+E3+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026
    48  #12  ML Engineer Apprentice Build AI; Remote

[All (1+2+3+4)]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    62  #8  PhD Intern, AI/ML/NLP Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026
    48  #12  ML Engineer Apprentice Build AI; Remote

[  E3 bm25-only]
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #11  Internship: AI/ML for Ionospheric TEC Modelling
    74  #3464  AI / ML Intern, Machine Learning (2026)
    80  #7  Internship @ Sqwish (AI Research / Backend / ML Engineering)
    28  #2  Member of Technical Staff (AI Inference Engineer)

[E1+E2+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    30  #26  Remote AI / ML Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026
    62  #8  PhD Intern, AI/ML/NLP Engineer

[E2 dimensions]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    70  #9  Internship Core Modeling AI/ML Software Engineer
    48  #12  ML Engineer Apprentice Build AI; Remote
    62  #8  PhD Intern, AI/ML/NLP Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[E1 keyword]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    62  #8  PhD Intern, AI/ML/NLP Engineer
    70  #9  Internship Core Modeling AI/ML Software Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026
    30  #26  Remote AI / ML Engineer

[E4 judge]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    30  #26  Remote AI / ML Engineer
    42  #1  Senior Specialist Solutions Engineer (AI/ML)
    80  #7  Internship @ Sqwish (AI Research / Backend / ML Engineering)
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026

[E1+E4]
    72  #1269  2026 Summer Intern – AI/ML Software Engineer (Master's)
    30  #26  Remote AI / ML Engineer
    74  #6  Applied Field Engineer AI/ML Intern (3 months) - London 2026
    70  #9  Internship Core Modeling AI/ML Software Engineer
    42  #1  Senior Specialist Solutions Engineer (AI/ML)
============================================================================================
