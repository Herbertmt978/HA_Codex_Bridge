# Scheduled tasks

Scheduled tasks run Codex through Home Assistant at the times you choose.
Your browser and PC can be closed. Home Assistant and the Codex Bridge App
must stay running, with ChatGPT signed in.

## Create a task

Choose **Describe a task** on the Scheduled page to write the timing and
instructions in one sentence. You can also write the request in a chat and
select **Schedule this message**. The message stays in your chat draft; it is
not sent as a chat turn. For example:

> Every weekday at 9 am, summarise yesterday's events.

You can put the task first for simple repeats, such as **Remind me to check
the heating every Monday at 2 pm**. Use **in this chat** immediately after the
time to propose continuing the current chat; otherwise the task starts a new
chat in the selected project. Supported timing includes daily, weekdays, a
named weekday, monthly on a day, a fixed interval with a start date, and a
one-off date. Write times as **09:00** or **9 am** and one-off dates as **24
September 2026** or **2026-09-24**. Numeric dates such as **09/10/2026** and
times such as **9** need clarification.

Select **Review timing** to open the normal task editor. Check the proposed
title, instructions, destination, next run times and Home Assistant time zone.
You can change anything before selecting **Create task**. Describing and
reviewing a task does not create or run it. A proposed task starts in Observe
mode; host access still needs its own explicit grant and unattended-use
acknowledgement. If your App does not advertise schedule proposals, use
**New schedule** until the paired App is updated.

You can also fill in the editor directly:

1. Select the project or chat you want to work in, then open **Scheduled**.
2. Choose **New schedule** and enter a **Scheduled task title**.
3. Describe what Codex should do. Include the outcome you want and any
   limits it should respect.
4. Under **Details**, choose **New chat** or **Current chat**. A new chat uses
   the selected project. Current chat continues the selected conversation.
5. Under **Frequency**, choose the repeat pattern and time. Check the preview
   beneath the card, then select **Create task**.

For example: title **Morning summary**, instructions **Summarise the new
information in this project's files and highlight anything needing attention**,
repeat **Weekdays**, time **09:00**. Describe only work the App can actually
access; it cannot read your PC or Home Assistant configuration by default.

## Frequency and time zone

- **Daily:** every day at the chosen time.
- **Weekdays:** Monday to Friday.
- **Weekly:** on the selected day of the week.
- **Monthly:** on the selected day of the month. Months without that date
  are skipped, so use day 28 or earlier if every month must have a run.
- **Every…:** a fixed interval measured from the start date and time.
- **Once:** one future date and time.

Times use the Home Assistant time zone shown in the preview, even if your
browser is in another zone. Daily and weekly schedules follow local clock
time; fixed intervals measure elapsed time. A time that does not exist when
clocks move forward is rejected when setting its start. When clocks move back,
an ambiguous start in the manual editor uses its first occurrence. The
description flow asks you to choose another time for an ambiguous one-off or
interval start. The next-run preview comes from the App's scheduler, so it
matches the times used when the browser is closed.

Existing custom schedules can be kept unchanged when editing the title or
instructions. Choose another Repeat option only when you intend to replace
their timing. The form preserves saved time zones and exact interval anchors.

## Permissions and results

**Advanced** contains permissions, model and reasoning overrides. Observe is
the default and keeps the workspace read-only. Edit and Full auto allow
workspace changes; Full auto changes approval handling, not the App's file or
network restrictions. Choose a model and reasoning level from the dropdowns,
or leave **Inherit** selected to use the target's defaults. Reasoning choices
follow the selected model. A saved choice that the runtime no longer advertises
is marked unavailable and kept until you change it.

**Full access · Home Assistant OS** requires the separate Host Access App and
the current administrator grant. Selecting it shows the root-access warning
and also asks you to allow this task to use those rights while you are absent.
Revoking access invalidates the task's saved selection. Enabling access again
does not restore old scheduled grants: edit the task and acknowledge it again.
See [Host Access](../codex_host_access_app/DOCS.md) before using this mode.

An unattended task cannot answer approval requests or questions. A run may be
stopped or skipped if it needs interaction, overlaps another run, exceeds
capacity or misses its scheduling window. Check **Runs** for the recorded
outcome. Successful responses appear in the task's chat. Configurable desktop
and mobile notifications are not available in this panel.

Use **Pause** before changing a workspace, updating the App or restoring a
backup. **Resume** enables future runs; **Run** requests a manual run.
**Update** edits the definition, and **Delete** removes it after confirmation.
