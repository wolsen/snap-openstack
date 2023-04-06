# Copyright (c) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import Mock

from sunbeam.jobs import checks


class TestSshKeysConnectedCheck:
    def test_run(self, mocker, snap):
        snap_ctl = Mock()
        mocker.patch.object(checks, "Snap", return_value=snap)
        mocker.patch.object(checks, "SnapCtl", return_value=snap_ctl)

        check = checks.SshKeysConnectedCheck()

        result = check.run()

        assert result is True

    def test_run_missing_interface(self, mocker, snap):
        snap_ctl = Mock(is_connected=Mock(return_value=False))
        mocker.patch.object(checks, "Snap", return_value=snap)
        mocker.patch.object(checks, "SnapCtl", return_value=snap_ctl)

        check = checks.SshKeysConnectedCheck()

        result = check.run()

        assert result is False
        assert "sudo snap connect mysnap:ssh-keys" in check.message
