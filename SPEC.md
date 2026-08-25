# SPEC — agent-lab

**Status: skeleton. This is yours to write, and it is week 1's real thinking work.**
Answer each question in prose, not bullets. If you cannot answer one, that is the thing to
resolve before writing the code it describes.

## 1. The problem, in one paragraph a stranger understands

<!-- Who has this problem, what goes wrong today, and what "solved" looks like. -->

## 2. What the agent is allowed to do

<!-- The exact list of tools. Three, not ten. What each one touches, and what it cannot undo. -->

## 3. What the agent must refuse

<!-- The policies, in plain English first. These become rows in the `policies` table in W3.
     Examples to make concrete: focus hours, external attendees, double-booking key people,
     recording consent, trainee meeting load. -->

## 4. Where the data comes from

<!-- Decided: your own personal calendar and your own transcripts. NOT ISPF data:
     this repo is public, and organisation meeting content must never enter it. -->

## 5. The schema, and why each table exists

<!-- Nine tables. For each: what one row means, and which later week reads it. -->

## 6. What "correct" means

<!-- How will you know the agent got it right? This paragraph becomes the eval spec in W4:
     task success, policy correctness, explanation quality. -->

## 7. What this deliberately does not do

<!-- Scope judgement reads as seniority. Name three things you are choosing not to build. -->

## 8. The five design decisions you expect to defend

<!-- For each: the alternative you rejected, and why. One of them is already made:
     the policy engine is deterministic Python, not a prompt. Write down why. -->
