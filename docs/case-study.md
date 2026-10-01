# Case study: delivery evidence for AI adoption

Author: Juan Carlos Orrego. Public reference implementation, October 2026.

## Problem

Organizations can see AI usage grow without a clear account of delivered work.
Repository identities differ across providers, bot accounts inflate raw interaction
counts, billing exports overlap, and hours-saved claims often lack an explicit baseline.
Leaders need a report they can inspect, explain, and calibrate with engineers.

## Design

This reference joins GitHub and Bitbucket activity through confirmed aliases, preserves
bot authorship, groups automation, and reconciles daily usage totals. It combines
observed activity with reviewed whole-PR effort ranges and an explicit historical
capacity model. Individual references fall back to a documented team mean when
history is insufficient. Missing evidence stays visible.

The tool has a provider-independent calculation engine, read-only collectors, a
synthetic interactive dashboard, an admin review flow with stale-revision protection,
versioned reports, and contract tests. Optional dollar scenarios stay behind a separate
disclosure. The public source and fixtures were written independently; no employer
application source, employee identity, real billing, or private screenshot is included.

## Evidence and limitations

The repository's CI and tests establish the behavior of its synthetic calculation,
collector, and access contracts. The synthetic example demonstrates reproducibility,
not organization adoption or financial impact. The loopback server is a reference
admin installation; enterprise SSO, tenant isolation, durable jobs, and production
audit storage are documented extensions. Provider access and complete billing coverage
must be verified in each organization.

## Suggested profile language

"Designed and built an open-source engineering delivery observatory combining GitHub
and Bitbucket activity, AI usage evidence, reviewed effort estimates, and versioned
historical capacity models with individual baselines and team fallback."

"Implemented explicit identity linking, human/bot separation, supported AI review
sponsorship, source coverage checks, and administrator approval controls."

These are implementation claims supported by the public repository. Do not add a
measured percentage gain, money saved, a customer count, or a claim of enterprise
deployment based on the fictional dashboard. An estimated historical equivalent
does not establish actual saved time or causal AI impact.
