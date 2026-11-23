# Bulk machine translation, 2026-11-16 (ut-docs#4371, #4372)

Owner decision 2026-11-16: bulk-accepted machine translation by owner decision 2026-11-16, to be refined; not human reviewed.

What was done: every empty non-legal till string of de, es, pt, fr, tr, ar, fa, ms and zh_Hans was translated by Claude (build-time, one fresh agent per language, checked with the project's own `bundle_checks.check_string`), saved as `translated` by the translator bot (`bot-universal-till-ai-translator-local`, the only translator account; there is no Claude translator account yet), reviewed by Codex (a different model family) and approved by `bot-universal-till-ai-reviewer-codex` through `tools/review_runner.py`. Strings Weblate only held as a Google suggestion were not accepted: the Claude text replaced them. Legal, money, tax, fiscal, receipt and price wording was NOT approved: it waits for a human (the Fable/legal step).

Weblate labels can only be set on source strings, so a per-translation label is not possible; this file is the record. The 8 per-language setting keys (`catalog.icon_match.head_last` etc.) were translated explicitly and checked by Codex (`catalog.icon_match.head_last`: de 1, zh_Hans 0 (Codex disagreed with 1; native check wanted), all others 0).

| language | bundle strings before | after (of 4080) | coverage after | approved by Codex | needs-human (not approved) | needs-fix (suggestion posted) |
|---|---|---|---|---|---|---|
| es | 3850 | 3997 | 98.0% | 0 | 59 | 2 |
| pt | 3850 | 3985 | 97.7% | 135 | 67 | 7 |
| de | 3842 | 3961 | 97.1% | 119 | 84 | 14 |
| fr | 3508 | 3723 | 91.2% | 215 | 91 | 29 |
| tr | 3733 | 3961 | 97.1% | 545 | 110 | 73 |
| ar | 3729 | 3957 | 97.0% | 228 | 83 | 20 |
| fa | 3731 | 3965 | 97.2% | 538 | 106 | 96 |
| ms | 3741 | 3941 | 96.6% | 200 | 98 | 27 |
| zh_Hans | 3694 | 3917 | 96.0% | 555 | 143 | 54 |

Floors: de, es, pt need 95% (3876); the others use the default.

## Keys left for a human (needs-human), by key, with the languages that wait

| key | languages |
|---|---|
| `android.what.same.d` | tr, fa, zh_Hans |
| `catalog.missing.price` | fr, tr, ar, fa, ms, zh_Hans |
| `catalog.missing.several` | fr, tr, ar, fa, ms, zh_Hans |
| `cta.title` | tr, fa, zh_Hans |
| `designer.receipt.voucher_slip_text` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `designer.receipt.voucher_slip_text_hint` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `designer.receipt.voucher_slip_text_too_long` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `dl.next` | tr, fa, zh_Hans |
| `dl.pilotlink` | fa |
| `elevation.summary.eod_day_x` | pt, de, fr |
| `elevation.summary.eod_day_x_print` | pt, de, fr |
| `elevation.summary.reduce_line_qty` | fr |
| `elevation.summary.shift_slip_print` | pt, de, fr, tr, ar, ms, zh_Hans |
| `elevation.summary.x_report` | pt, de, fr, tr, ar, ms, zh_Hans |
| `elevation.summary.x_report_print` | pt, de, fr, tr, ar, ms, zh_Hans |
| `faq.a1` | tr, fa, zh_Hans |
| `faq.a4` | tr, fa, zh_Hans |
| `faq.a6` | tr, fa, zh_Hans |
| `faq.q2` | fa, zh_Hans |
| `faq.q5` | fa, zh_Hans |
| `faq.sub` | tr, fa, zh_Hans |
| `foot.tagline` | fa |
| `free.title` | fa |
| `get.s3.d` | tr, fa, zh_Hans |
| `help.search.placeholder` | tr, fa, zh_Hans |
| `hero.title` | tr, fa, zh_Hans |
| `manage.catalog.d` | tr, fa, zh_Hans |
| `manage.mods.d` | tr, fa, zh_Hans |
| `notices.expires` | fr, tr |
| `pilot.badge` | tr, zh_Hans |
| `pilot.lede` | tr, zh_Hans |
| `pluginspage.free` | tr, zh_Hans |
| `pluginspage.kind.payment` | tr |
| `pluginspage.price` | tr, fa, zh_Hans |
| `promotions.col.dates` | ms, zh_Hans |
| `promotions.deactivate_confirm` | zh_Hans |
| `promotions.empty` | zh_Hans |
| `promotions.search_placeholder` | zh_Hans |
| `receipt.printer.ask_manager` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `receipt.printer.nothing_printed` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `receipt.printer.set_up` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.eod.day_x.as_of` | es, tr |
| `reports.eod.day_x.paper.title` | es, pt, fa |
| `reports.eod.day_x.paper_label` | es |
| `reports.eod.day_x.print` | es, fa |
| `reports.eod.day_x.print_failed` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.eod.day_x.print_ok` | es, fa |
| `reports.eod.day_x.show` | fa |
| `reports.eod.day_x.since` | es, de, tr, fa, ms |
| `reports.eod.day_x.since_first` | es, de, ms |
| `reports.eod.day_x.title` | es, fa |
| `reports.eod.fiscal_device.mismatch_count` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.eod.no_sale_opens` | ms |
| `reports.eod.no_sale_title` | ms |
| `reports.eod.no_sale_total` | es, pt, de, fr, tr, ar, fa, ms |
| `reports.eod.x_report_link` | es, de, fa, ms |
| `reports.online_channels.col.currency` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.online_channels.col.gross` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.online_channels.col.net` | es, pt, de, fr, ar, fa, ms, zh_Hans |
| `reports.online_channels.col.refunds` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.online_channels.currency_total` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.online_channels.notice` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.online_channels.refunds_note` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.zpaper.no_sale_title` | ms |
| `reports.zpaper.no_sale_total` | ar, ms |
| `reports.zpaper.restored_receipts` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `reports.zpaper.restored_z` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `settings.backup.upload.no_sales` | zh_Hans |
| `settings.currency_display.auto` | fr, tr, ar, fa, ms, zh_Hans |
| `settings.currency_display.code` | fr, tr, ar, fa, ms, zh_Hans |
| `settings.currency_display.help` | fr, tr, ar, fa, ms, zh_Hans |
| `settings.currency_display.symbol` | fr, tr, ar, fa, ms, zh_Hans |
| `settings.currency_display.title` | fr, tr, ar, fa, ms, zh_Hans |
| `settings.enrol.pair_replace_body` | zh_Hans |
| `settings.enrol.terms.accept_btn` | de |
| `settings.enrol.terms.accept_required` | de |
| `settings.enrol.terms.accepted` | de |
| `settings.enrol.terms.label` | de |
| `settings.enrol.terms.link` | de |
| `settings.enrol.terms.send_failed` | de |
| `settings.enrol.terms.unsent` | de |
| `settings.enrol.terms_required` | de |
| `settings.move.confirm_body` | zh_Hans |
| `settings.move.confirm_title` | zh_Hans |
| `settings.move.done` | zh_Hans |
| `settings.move.gone` | zh_Hans |
| `settings.move.notice_cancelled` | zh_Hans |
| `settings.move.notice_expired` | zh_Hans |
| `settings.move.notice_locked` | zh_Hans |
| `settings.move.notice_pending` | zh_Hans |
| `settings.move.refused_currency` | fr, tr, ar, fa, ms, zh_Hans |
| `settings.move.refused_fiscal_device` | fr, tr, ar, fa, ms, zh_Hans |
| `settings.move.refused_paid_licence` | fr, tr, ar, fa, ms, zh_Hans |
| `settings.printer.system_unavailable` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `settings.printer.voucher_slip` | es, pt, de, fa, ms, zh_Hans |
| `settings.printer.voucher_slip_hint` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `setup.next_steps.devices` | fr, tr, ar, fa, ms, zh_Hans |
| `shifts.adjustment_confirmed` | de, ar, ms, zh_Hans |
| `shifts.close_no_sale_opens` | fr, ms, zh_Hans |
| `shifts.close_slip.failed` | pt, de, fr, ar, ms, zh_Hans |
| `shifts.close_slip.paper.section_close` | pt, de, fr, tr, ar, ms, zh_Hans |
| `shifts.close_slip.paper.title` | pt, de, fr, tr, ar, ms, zh_Hans |
| `shifts.close_slip.print` | pt, de, fr, tr, ar, ms, zh_Hans |
| `shifts.close_slip.print_failed` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.close_slip.print_ok` | pt, de, fr, tr, ar, ms, zh_Hans |
| `shifts.close_slip.printing` | de |
| `shifts.count_detail.denomination` | pt, de, fr, ar, zh_Hans |
| `shifts.count_detail.failed` | pt, de, fr, ar, zh_Hans |
| `shifts.count_detail.hint` | pt, de, fr, ar, ms, zh_Hans |
| `shifts.count_detail.mismatch` | es, pt, de, fr, tr, ar, ms, zh_Hans |
| `shifts.count_detail.none` | pt, de, fr, ar, ms, zh_Hans |
| `shifts.count_detail.partial` | pt, de, fr, ar, ms, zh_Hans |
| `shifts.count_detail.pieces` | pt, de, ar, ms, zh_Hans |
| `shifts.count_detail.show` | pt, de, ar, ms, zh_Hans |
| `shifts.count_detail.title` | pt, de, fr, ar, ms, zh_Hans |
| `shifts.count_detail.total` | es, pt, de, fr, ar, ms, zh_Hans |
| `shifts.count_detail.unreadable` | pt, de, fr, ar, ms, zh_Hans |
| `shifts.x_report.adjustments_total` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.cash_sales` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.cleared` | fr, ms |
| `shifts.x_report.count` | pt, fr, ms |
| `shifts.x_report.failed` | pt, de, fr, ms, zh_Hans |
| `shifts.x_report.first_receipt` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.generated_at` | es, pt, de, ms, zh_Hans |
| `shifts.x_report.hide` | pt, de, fr, ms |
| `shifts.x_report.invalid_register` | ms |
| `shifts.x_report.last_receipt` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.method` | es, pt, de, fr, tr, ar, zh_Hans |
| `shifts.x_report.no_payments` | es, pt, de, fr, tr, ar, ms, zh_Hans |
| `shifts.x_report.not_a_close` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.paper.not_a_close` | tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.paper.title` | es, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.print` | es, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.print_failed` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.print_ok` | es, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.printing` | es |
| `shifts.x_report.received` | es, fr, tr |
| `shifts.x_report.refunded` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.returns` | es, de, fr, tr, ar, fa, ms |
| `shifts.x_report.sales` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.section_cash` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.section_tenders` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.show` | es, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.tax` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.title` | es, fr, tr, ar, fa, ms, zh_Hans |
| `shifts.x_report.voids` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `site.legal_english_binding` | tr, fa, zh_Hans |
| `sol.retail.d` | tr, fa, zh_Hans |
| `sol.service.d` | tr, fa, zh_Hans |
| `start.dev.pi.d` | tr, fa, zh_Hans |
| `start.mac.2` | tr, fa, zh_Hans |
| `start.s4.d` | fa |
| `start.sell.ring.d` | tr, fa, zh_Hans |
| `start.sell.ring.t` | fa |
| `start.w2.d` | tr, fa, zh_Hans |
| `subscription.check_result.still_lapsed_ios` | pt, de |
| `tills.join_error.unsent_day_closes_refused_ios` | es, pt, de, tr, fa, ms, zh_Hans |
| `tills.promote_adopt_voided` | zh_Hans |
| `tills.promote_outside_z` | fr, tr, fa, ms, zh_Hans |
| `tills.sales_mirror.help` | fr, tr, ar, fa, ms, zh_Hans |
| `tour.catalog.d` | tr, fa, zh_Hans |
| `tour.reports.d` | tr, fa, zh_Hans |
| `tour.sale.d` | zh_Hans |
| `vouchers.print_slip.browser_hint` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `vouchers.print_slip.browser_opened` | de, fr, tr, ms, zh_Hans |
| `vouchers.print_slip.button` | de, fr, ar, fa, ms, zh_Hans |
| `vouchers.print_slip.done` | de, fr, tr, ar, ms, zh_Hans |
| `vouchers.print_slip.no_printer` | de, fr, tr, ar, ms, zh_Hans |
| `vouchers.print_slip.no_printer_hint` | de, fr, tr, ar, ms, zh_Hans |
| `vouchers.print_slip.not_printable` | pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `vouchers.print_slip.original_value_note` | es, pt, de, fr, tr, ar, fa, ms, zh_Hans |
| `vouchers.print_slip.printing` | ar, ms, zh_Hans |
| `vouchers_import.status.bad_balance` | fr |
| `vouchers_import.status.non_positive_balance` | fr |
| `why.payments.t` | fa, zh_Hans |
| `wiz.gap.cardother` | zh_Hans |
| `wiz.hw.need` | tr, fa, zh_Hans |
| `wiz.n.cardnone` | tr, zh_Hans |
| `wiz.o.card.none` | zh_Hans |
| `wiz.o.card.other` | tr, zh_Hans |
| `wiz.o.needs.fiscal` | tr, fa, zh_Hans |
| `wiz.plugin.payment-stripe` | fa |
| `wiz.plugin.payment-sumup` | fa |
| `wiz.price.free` | tr, fa, zh_Hans |
| `wiz.price.pick` | tr, fa, zh_Hans |
| `wiz.price.talk` | tr, fa, zh_Hans |
| `wiz.price.unpub` | tr, fa, zh_Hans |
| `wiz.q.country.hint` | tr, fa, zh_Hans |
| `wiz.r.multi` | tr, fa, zh_Hans |
