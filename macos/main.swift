// Second Hand Smoke — verified card offload for camera and sound media.
// Copyright (C) 2026  CnF Works
//
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License as published by the Free
// Software Foundation, either version 3 of the License, or (at your option)
// any later version.
//
// This program is distributed in the hope that it will be useful, but WITHOUT
// ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
// FOR A PARTICULAR PURPOSE.  See the GNU General Public License for details.
//
// You should have received a copy of the GNU General Public License along with
// this program.  If not, see <https://www.gnu.org/licenses/>.

// Explicit app startup avoids Swift's @main/AppKit delegate ambiguity when the
// wrapper is compiled directly with swiftc rather than through an Xcode target.

import Cocoa

let app = NSApplication.shared
let delegate = SecondHandSmokeApp()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
