# -*- coding: utf-8 -*-
"""The timetable platform: everything that is not the scheduling engine.

The engine (`model`, `checks`, `solver`, `validate`, `report`, `polish`,
`word`) knows one school at a time and reads it from module globals.  This
package is what makes that engine serve many schools: a `SchoolSpec` carries
a whole school as data, `activate` installs one into the engine for the
duration of a call, and the rest of the package is the product built on top —
import, a constraint screen, editing, alternatives, export and a web server.
"""
