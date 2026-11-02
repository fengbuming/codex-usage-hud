# Third-Party Notices

## Agent Speed vehicle line art and Liquid orb design

The 13 vehicle outlines used by the Renderer speed indicator are adapted from
`spec/assets/vehicles.json` in [zoefix/agent-speed](https://github.com/zoefix/agent-speed),
licensed under the MIT License.

The completed session bubble's liquid level, layered waves and rising microbubbles
are adapted from Agent Speed's `spec/DESIGN.md` (section 2a.3) and
`windows/src/AgentSpeed.Presentation/Orb/Styles/LiquidModel.cs`, reviewed at commit
`384ce020cc655133e95aaad5cc085f3985f0ed99`. The adaptation uses final session
average output speed and the approved 400 tok/s visual scale. Waves and
microbubbles animate continuously while the completed badge is visible,
pausing when it is hidden; the final average speed remains fixed.

Copyright (c) 2026 zoefix

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
