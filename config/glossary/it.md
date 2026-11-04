# Italian (it) glossary and style

Used by the build-time translator (a Claude model) and by the human spot
check (ut-docs#5421). Weblate's own glossary component is empty; this file is
the agreed source until it is loaded there.

## Register

- Informal **tu** (like ut-cloud's it-IT): "puoi", "il tuo negozio", "Inserisci...". Never "Lei".
- Short imperative UI text on a touchscreen till: "Aggiungi", "Salva", "Annulla". Sentence case. No exclamation marks unless the English has one.
- Gender-neutral where it is cheap: "Cassiere/a" only when a label is the role itself; otherwise rephrase ("Chi usa la cassa").
- Italian typography: decimal comma and thousands dot belong to the formatter, never typed into a string. Keep the English punctuation style of placeholders. "e" not "&" in running text; "&" may stay in short tab labels if the English has it.
- Keep the Italian within the max-length flag; abbreviate before dropping meaning ("gg" days, "min").

## Never translate

`Universal Till`, plugin names, brand and partner names (SumUp, Stripe, Zitadel, Shopify...), SKU, PIN, QR, EAN-13, Code 128, Wi-Fi, Bluetooth, file formats (CSV, PDF), keyboard key names written as code, anything inside `%s`, `%[1]d`, `{{.X}}`, `{0}`, `{name}`, ICU `{count, plural, ...}` (keep the argument names; translate only the text arms), HTML tags and link targets.

## Terms

| English | Italian | Note |
|---|---|---|
| till (the device / POS) | cassa (pl. casse) | not "registratore" |
| Universal Till | Universal Till | product name |
| shop / store | negozio | |
| owner | titolare | |
| manager | responsabile | ut-cloud uses "gestore" for the cloud manager app; a shop role is "responsabile" |
| cashier | cassiere/a | plural "cassieri" |
| staff / worker | personale | "membro del personale" for one person; never "collaboratore" |
| sale | vendita | |
| order | ordine | |
| product / item | articolo | "prodotto" only where the English says product in a retail catalog context |
| catalog | catalogo | |
| category | categoria | |
| variant | variante | |
| barcode | codice a barre | |
| stock | scorte | stock level = "livello di scorta" |
| receipt | scontrino | invoice = "fattura", tax invoice = "fattura" |
| refund | rimborso | returned goods = "reso" |
| void | annullo | a voided line/sale = "annullato"; "storno" for reversing an entry |
| discount | sconto | |
| tip | mancia | |
| voucher / gift card | buono / carta regalo | |
| payment / tender | pagamento | tender type = "metodo di pagamento" |
| cash drawer | cassetto | "cassetto contanti" when needed |
| quick buttons | pulsanti rapidi | |
| hold / on hold | metti in attesa / in attesa | held sale = "vendita in attesa" |
| resume | riprendi | |
| shift | turno | |
| report | report | masculine, plural "report" |
| settings | impostazioni | |
| plugin | plugin | masculine, plural "plugin" |
| supplier | fornitore | |
| customer | cliente | |
| tax / VAT | imposta / IVA | "aliquota IVA" = tax rate |
| print / printer | stampa / stampante | |
| device | dispositivo | |
| pair / pairing | associa / associazione | a till paired to the main till |
| main till / satellite | cassa principale / cassa satellite | ADR-0086; never "master/slave" |
| self-order | self-order | kiosk ordering; "ordina da solo" is too long |
| sync | sincronizzazione | verb "sincronizza" |
| offline | offline | |
| sign in / sign out | accedi / esci | |
| save / cancel / delete | salva / annulla / elimina | |
| back-office | back office | |
| register (cash register / till position, `registers.*`) | postazione cassa (pl. postazioni cassa) | DECIDED 2026-11 (tester defects, #5421). Never "registro di cassa" (= cash ledger) and never "registratore" (= the physical machine). Same term in settings, staff, shifts. |
| location (stock location) | sede (pl. sedi) | never "ubicazione" |
| Paid (order payment status, `channel_orders.payment.paid`) | Pagato | never "A pagamento" (= "for a fee"). "A pagamento" is right only for the store badge meaning a paid plugin (priced, not free). |
| tax added at checkout | imposta aggiunta al momento del pagamento | say "imposta", not "IVA", where the setting also covers GST/sales-tax countries; "alla cassa" is ambiguous (till vs checkout) |
| size examples | Piccolo | masculine default, e.g. "es. Piccolo" |

## Review notes

- Strings seeded from ut-cloud's it-IT (exact English match) were written by an
  earlier Italian pack, then re-saved by the translator bot; short ones ("Status",
  "Item") may need context.
- Legal, receipt-legal and fiscal wording is drafted in
  `ut-docs/docs/legal-review/it-legal-review-sheet.md` and is not in Weblate.
- Receipt wording on legal/fiscal strings ("Ricevuta ... non valida ai fini fiscali" vs plain "scontrino") is an open owner question in the legal-review sheet; non-legal UI keeps "scontrino".
