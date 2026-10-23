"""Sheet Nester - lay flat panels on MDF/plywood sheets and nest them for cutting."""

import traceback

import adsk.core

from . import sn_command


def run(context):
    try:
        sn_command.start()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(
            'Sheet Nester failed to start:\n\n%s' % traceback.format_exc())


def stop(context):
    try:
        sn_command.stop()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(
            'Sheet Nester failed to stop:\n\n%s' % traceback.format_exc())
