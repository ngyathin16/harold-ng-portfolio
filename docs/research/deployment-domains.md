# Portfolio domain research

Checked 16 November 2026, Hong Kong time. Scope: one personal portfolio domain, with the user-confirmed **HK$200 annual cap including renewal**. This note supports the deployment PRD; no domain was purchased, reserved, or added to an authenticated checkout.

Recommend **Cloudflare Registrar** alongside the proposed Cloudflare Workers Static Assets deployment, provided the chosen name is available at standard pricing and its actual first-year and renewal totals fit the cap. Start with the user's preferred `harold.work`; retain `haroldng.work`, `haroldng.dev`, and `haroldng.com` as alternatives. This is a recommendation about account simplicity and the prices below, not a claim that any exact name is available.

The following are published extension prices for one year, in US dollars. They are **standard, non-premium benchmarks**, not exact-domain quotes. Renewal means the rate displayed today, not a guarantee of the rate charged in a future year. Cloudflare's `.dev` registration cell displays US$8.20 alongside US$12.20; treat the lower figure as the advertised introductory price and confirm its applicability at checkout. [Cloudflare extension pricing](https://pricing.registrar.cloudflare.com/)

| Registrar | Extension | First year shown | Regular registration price | Current annual renewal |
| --- | --- | ---: | ---: | ---: |
| Cloudflare | `.work` | US$10.20 | US$10.20 | US$10.20 |
| Cloudflare | `.dev` | US$8.20 introductory | US$12.20 | US$12.20 |
| Cloudflare | `.com` | US$11.17 | US$11.17 | US$11.17 |
| Porkbun | `.work` | US$2.06 sale | US$10.81 | US$10.81 |
| Porkbun | `.dev` | US$8.75 sale | US$12.87 | US$12.87 |
| Porkbun | `.com` | US$11.81 | US$11.81 | US$11.81 |

Sources: [Cloudflare's live table](https://pricing.registrar.cloudflare.com/) and [Porkbun's live table](https://porkbun.com/products/domains). Porkbun explicitly labels its prices as USD, non-premium, single-year amounts including ICANN and other fees; its `.work` and `.dev` reductions apply to the first year. Cloudflare describes registration and renewal as registry and ICANN costs without markup. That policy alone is not a numeric quote: the table above supplies the observed figures. [Cloudflare pricing policy](https://developers.cloudflare.com/registrar/faq/#billing)

For budgeting only, multiplying USD by **HK$8 per US$1** gives renewal estimates of HK$81.60, HK$97.60, and HK$89.36 respectively at Cloudflare; at Porkbun, HK$86.48, HK$102.96, and HK$94.48. This is a deliberately simple planning assumption, not a live exchange-rate quotation. All six benchmarks leave room within HK$200, but the actual payment conversion, card foreign-currency fee, and any checkout tax must be included in the final cap check. No Hong Kong checkout tax amount or exact card settlement amount was verified. Do not approve a purchase solely from these estimates.

Porkbun is a credible alternative: its `.work` first year is US$8.14 cheaper, while its annual renewal is only US$0.61 higher, calculated from the cited tables. It also allows changing authoritative nameservers to another DNS provider. Cloudflare Registrar requires Cloudflare nameservers; moving authoritative DNS elsewhere requires transferring registration out. For the proposed Cloudflare deployment, keeping registration and DNS together is the recommended convenience tradeoff. Registrar choice should not force paid hosting into the budget. [Porkbun nameserver settings](https://kb.porkbun.com/article/22-how-to-change-nameservers), [Cloudflare nameserver requirement](https://developers.cloudflare.com/registrar/faq/#can-i-use-my-own-third-party-nameservers)

**Exact names remain unverified:** `harold.work`, `haroldng.work`, `haroldng.dev`, and `haroldng.com` each need a fresh registrar availability check and individual first-year/renewal quote. A missing website or DNS response is not an availability result. Registry premiums can affect registration, renewal, or both; Porkbun notes that short names and common personal names are more likely to be premium. This makes `harold.work` worth checking carefully, without asserting that it is premium. [Premium pricing explanation](https://kb.porkbun.com/article/41-what-are-premium-domains)

Name preference is a branding judgment: `.work` suits a portfolio across disciplines; `.dev` emphasizes software work; `.com` is the general-purpose alternative. If choosing `.dev`, HTTPS and a valid certificate must work before launch because browsers enforce HTTPS through the extension's HSTS preload entry. [Google Registry's `.dev` requirements](https://www.registry.google/domains/dev/)

For later deployment, the approval record should identify the exact spelling, registrar, standard/premium status, one-year registration total, current one-year renewal total, and expected HKD charge including applicable tax and payment fees. Both totals must fit HK$200, and the user must approve that final quote before purchase. If the preferred name fails either check, return the quoted alternatives instead of buying an expensive premium name or multiple defensive domains.

After an approved purchase, keep automatic renewal enabled and maintain a valid payment method and accessible contact email. Cloudflare enables auto-renew by default and starts attempts roughly 30 days before expiry; successful payment still needs checking. Porkbun also enables it by default and documents a stored credit card for automatic renewal. Record a reminder before Cloudflare's renewal window to review the current price against the annual cap. [Cloudflare renewal timing](https://developers.cloudflare.com/registrar/account-options/renew-domains/), [Porkbun default setting](https://kb.porkbun.com/article/70-how-to-turn-off-auto-renew), [Porkbun renewal payment requirement](https://kb.porkbun.com/article/45-how-to-renew-a-domain)
