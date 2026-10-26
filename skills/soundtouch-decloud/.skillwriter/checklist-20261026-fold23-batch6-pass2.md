# skill-writer checklist - soundtouch-decloud (memory rules folded in)

- [x] Rule: Notice that the failing stations are HTTPS and the working ones plain HTTP, and read
      the speaker's clock first: it has no battery-backed RTC or NTP, so a power cut resets it to
      years in the past and TLS cannot validate. Read it over ssh (or from the Date header of its
      :8090 web server if it has no shell) and set it with a one-shot ntpd query against the LAN
      gateway, knowing the fix does not persist on the speaker.
      Where: no text change (already covered)
- [x] Rule: Ping the suspect and a standby neighbour side by side for about 60 s and watch the
      suspect's tx_failed counter. Late but complete replies mean it is asleep, so leave it. Near-
      total loss with tx_failed climbing means a wedged radio: send 'sys reboot' to port 17000
      with retries and confirm by uptime, then keep sampling for at least an hour, because a
      recurrence shows the reboot is no remedy and cannot separate the speaker from its location.
      Where: troubleshooting.md, ## Symptom to cause (new table row after 'One speaker missing, others
      found') + new section '## A standby speaker stops answering: asleep or a wedged radio'
      before '## The registry names another host'
- [x] Receipt held (the coordinator ran skill_receipt.py start meta-skill-writer); every editing
      agent loaded the skill through the Skill tool before its first edit.
- [x] RED as a text check: an opus quote-back against a pre-edit snapshot of the skill. The rules
      under test are recorded in this machine's memory store, so a behavioural arm would be
      contaminated; only a verbatim quote from the snapshot counts.
- [x] GREEN quote-back on haiku and opus against the edited files (6 questions), again after the
      review fixes for every question, and a third time for the 1 questions the fix-delta fixes
      touched: every answer quoted the edited text, no NONE. Quotes were substring-checked against
      the files; the pre-edit snapshots fail the same check.
- [x] GREEN diffed against RED both ways: every partial RED quote is still in the edited text.
- [x] Tiers haiku and opus, one cell per question per tier per round.
- [x] Independent review of the full diff, then of the fix delta alone; every finding fixed or
      declined with evidence by the fixer, and the fix delta re-reviewed.
- [x] Each GREEN Skill gaps list triaged: 6 closed in the text, 11 declined with a reason (covered
      elsewhere with file:line, out of scope, owned by another skill, or a judgement the skill
      leaves open).
- [x] Description measured: unchanged (frontmatter not edited).
- [x] ASCII only, present tense, tables canonical (reformat_tables.py --check --strict), added
      prose wrapped like the surrounding text, no code span split across lines.
- [x] Security scan: prose only; no secrets, hostnames, private paths or PII; addresses are
      documentation ranges or placeholders.
