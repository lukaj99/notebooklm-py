#!/bin/bash
# Batch add CCB/BB overdose literature sources to NotebookLM
# Sources prioritized by importance: guidelines, systematic reviews, landmark studies

set -e
source .venv/bin/activate

echo "=== Adding Tier 1: Guidelines & Systematic Reviews ==="

notebooklm source add "https://doi.org/10.1161/CIR.0000000000001161" 2>&1 | tail -1
sleep 3
# AHA 2023 Focused Update - Lavonas et al.

notebooklm source add "https://doi.org/10.3109/15563650.2014.965827" 2>&1 | tail -1
sleep 3
# St-Onge 2014 - CCB poisoning treatment SR

notebooklm source add "https://doi.org/10.1080/15563650.2020.1752918" 2>&1 | tail -1
sleep 3
# Rotella 2020 - BB poisoning treatment SR

notebooklm source add "https://doi.org/10.1081/clt-120023761" 2>&1 | tail -1
sleep 3
# Bailey 2003 - Glucagon SR

echo "=== Adding Tier 2: Comprehensive Reviews ==="

notebooklm source add "https://doi.org/10.2165/00139709-200423040-00003" 2>&1 | tail -1
sleep 3
# DeWitt 2004 - CCB & BB toxicity pharmacology/review

notebooklm source add "https://doi.org/10.1093/ehjacc/zuad138" 2>&1 | tail -1
sleep 3
# Goldfine 2023 - Current evidence review

notebooklm source add "https://doi.org/10.1111/bcp.12763" 2>&1 | tail -1
sleep 3
# Graudins 2016 - Antidotes and adjunct therapies

notebooklm source add "https://doi.org/10.2146/AJHP060041" 2>&1 | tail -1
sleep 3
# Shepherd 2006 - Treatment of BB/CCB poisoning

echo "=== Adding Tier 3: High-Dose Insulin (HIE/HDI) ==="

notebooklm source add "https://doi.org/10.3109/15563650.2011.582471" 2>&1 | tail -1
sleep 3
# Engebretsen 2011 - HDI therapy

notebooklm source add "https://doi.org/10.1186/cc4938" 2>&1 | tail -1
sleep 3
# Lheureux 2006 - HIE bench-to-bedside

notebooklm source add "https://doi.org/10.1186/2008-2231-22-36" 2>&1 | tail -1
sleep 3
# Woodward 2014 - HDI evidence-based

notebooklm source add "https://doi.org/10.1111/1742-6723.70035" 2>&1 | tail -1
sleep 3
# Isoardi 2025 - HDI is inodilator not antidote (controversy)

notebooklm source add "https://doi.org/10.1002/bcp.70081" 2>&1 | tail -1
sleep 3
# Chan 2025 - HDI in DHP-CCB hypotension

echo "=== Adding Tier 4: Lipid Emulsion (ILE) ==="

notebooklm source add "https://doi.org/10.1111/j.1553-2712.2009.00499.x" 2>&1 | tail -1
sleep 3
# Cave 2009 - ILE systematic review

notebooklm source add "https://doi.org/10.1186/1757-7241-18-51" 2>&1 | tail -1
sleep 3
# Rothschild 2010 - ILE in clinical toxicology

notebooklm source add "https://doi.org/10.1016/j.jemermed.2013.08.135" 2>&1 | tail -1
sleep 3
# Doepker 2014 - HDI + ILE case series

echo "=== Adding Tier 5: ECMO/ECLS ==="

notebooklm source add "https://doi.org/10.1177/0267659113498807" 2>&1 | tail -1
sleep 3
# Weinberg 2014 - VA-ECMO amlodipine overdose

notebooklm source add "https://doi.org/10.4103/ijccm.IJCCM_417_17" 2>&1 | tail -1
sleep 3
# Vignesh 2018 - ECMO in drug overdose case series

notebooklm source add "https://doi.org/10.5847/wjem.j.1920-8642.2022.070" 2>&1 | tail -1
sleep 3
# Vandroux 2022 - Predicting ECMO need

echo "=== Adding Tier 6: Methylene Blue & Levosimendan ==="

notebooklm source add "https://doi.org/10.1136/bcr-2012-007402" 2>&1 | tail -1
sleep 3
# Aggarwal 2013 - MB reverses shock

notebooklm source add "https://doi.org/10.1080/15563650.2016.1180390" 2>&1 | tail -1
sleep 3
# Warrick 2016 - MB systematic analysis

notebooklm source add "https://doi.org/10.1016/j.annemergmed.2014.09.015" 2>&1 | tail -1
sleep 3
# Jang 2014 - MB porcine model

notebooklm source add "https://doi.org/10.1213/ane.0b013e3181931737" 2>&1 | tail -1
sleep 3
# Varpula 2009 - Levosimendan

echo "=== Adding Tier 7: Epidemiology & Outcomes ==="

notebooklm source add "https://doi.org/10.1080/15563650.2020.1826504" 2>&1 | tail -1
sleep 3
# Huang 2020 - Angiotensin axis + DHP-CCB instability

notebooklm source add "https://doi.org/10.1007/s13181-011-0209-8" 2>&1 | tail -1
sleep 3
# Truitt 2012 - Unintentional BB/CCB overdose outcomes

notebooklm source add "https://doi.org/10.1016/j.annemergmed.2013.03.018" 2>&1 | tail -1
sleep 3
# Levine 2013 - Verapamil/diltiazem 25-year experience

echo ""
echo "=== All sources added ==="
