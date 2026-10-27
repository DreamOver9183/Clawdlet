"""FSM state names shared by the controller and the behavior table."""

from __future__ import annotations

from enum import Enum


class PetState(str, Enum):
    """Behavior states for Clawdlet (value = sprite strip name)."""

    IDLE = "idle_breathe"
    BLOB = "idle_blob"
    WANDER = "walk_horizontal"
    TYPING = "typing_laptop"
    PUT_AWAY_LAPTOP = "put_away_laptop"
    KICK_LAPTOP = "kick_laptop"
    CLIMB = "climb_vertical"
    PERCH_CLAUDE = "perch_look_down"
    DRAGGED = "dragged_struggle"
    FALLING = "free_fall"
    THROWN = "thrown"
    LAND_SQUISH = "land_squish"
    DIZZY = "dizzy"
    # Milestone 5 interactions
    CHASE_CURSOR = "chase_cursor"
    POUNCE = "pounce"
    POKED = "poked"
    PETTED = "petted"
    FLAIL = "flail"
    YAWN = "yawn"
    SLEEP = "sleep"
    WAKE_STARTLE = "wake_startle"
    PEEK_EDGE = "peek_edge"
    CELEBRATE = "celebrate"
    ERROR_SWEAT = "error_sweat"
    ALERT = "alert"
    PUSH_WINDOW = "push_window"
    # Milestone 6 work sessions
    WORK_GLANCE = "work_glance"
    WORK_STRETCH = "work_stretch"
    WORK_THINK = "work_think"
    WORK_SIP = "work_sip"
    JUGGLE = "juggle"            # Subagents running: juggle them at the laptop
    # Milestone 6 Claude Code reactions
    MOLT = "molt"                # The conversation was compacted: shed the old shell
    CONFUSED = "confused"        # First tool failure in a streak
    SAD = "sad"                  # Third failure in a streak, or a turn that failed outright
    # Milestone 6 tiered idle (L2, after the user has been away a while) and position-aware actions
    READ_SCROLL = "read_scroll"    # Unroll a little scroll and read it
    POLISH_CLAW = "polish_claw"    # Buff a claw to a shine
    DIG_SAND = "dig_sand"          # Dig in the sand (floor only)
    PERCH_DANGLE = "perch_dangle"  # Sit at the end of the title bar, legs dangling over its edge
    PERCH_PEER = "perch_peer"      # Lean over the title bar to peer into the window at the cursor
    WALL_SCRATCH = "wall_scratch"  # Scratch at a screen wall / drum on the side of Claude's window
    # Milestone 6 entrances, goodbye, mail delivery and listening
    ENTER_WALK = "enter_walk"      # Walk in from beyond a screen edge (renders walk_horizontal)
    GOODBYE = "wave_goodbye"       # Tray Exit: wave goodbye before the app closes
    MAIL_CARRY = "walk_mail"       # A turn finished while you were away: bring the envelope over
    MAIL_WAIT = "hold_mail"        # Hold the envelope up near the cursor until clicked
    MAIL_GIVE = "give_mail"        # Hand the envelope over (then Claude Desktop comes to the front)
    LISTEN = "listen_tilt"         # Tilt the head toward Claude's window while you type into it
    # Notifications: Claude waits for a plan review, an answer or a permission (the strip shown
    # depends on what it waits for; these values are the plan versions)
    NOTIFY = "notify_plan"         # Hold the sign up where Clawd stands
    NOTIFY_SEEK = "walk_notify_plan"  # Unanswered: carry the sign over to the cursor
    NOTIFY_WAIT = "notify_wait"    # Hold the sign up next to the cursor (renders notify_*)

    @property
    def animation_name(self) -> str:
        # THROWN is a distinct FSM state (ends in DIZZY) but shares the free-fall sprite
        if self is PetState.THROWN:
            return PetState.FALLING.value
        if self is PetState.CHASE_CURSOR or self is PetState.ENTER_WALK:
            return PetState.WANDER.value
        if self is PetState.NOTIFY_WAIT:
            return PetState.NOTIFY.value
        return self.value
