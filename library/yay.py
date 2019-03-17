#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2012, Afterburn <https://github.com/afterburn>
# Copyright: (c) 2013, Aaron Bull Schaefer <aaron@elasticdog.com>
# Copyright: (c) 2015, Indrajit Raychaudhuri <irc+code@indrajit.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = '''
---
module: yay 
short_description: Manage packages with I(yay)
description:
    - Manage packages with the I(yay) package manager, which is used by Arch Linux and its variants.
version_added: "1.0"
author:
    - Indrajit Raychaudhuri (@indrajitr)
    - Aaron Bull Schaefer (@elasticdog) <aaron@elasticdog.com>
    - Maxime de Roucy (@tchernomax)
options:
    name:
        description:
            - Name or list of names of the package(s) or file(s) to install, upgrade, or remove.
              Can't be used in combination with C(upgrade).
        aliases: [ package, pkg ]

    state:
        description:
            - Desired state of the package.
        default: present
        choices: [ absent, latest, present ]

    recurse:
        description:
            - When removing a package, also remove its dependencies, provided
              that they are not required by other packages and were not
              explicitly installed by a user.
              This option is deprecated since 2.8 and will be removed in 2.10,
              use `extra_args=--recursive`.
        default: no
        type: bool
        version_added: "1.3"

    force:
        description:
            - When removing package, force remove package, without any checks.
              Same as `extra_args="--nodeps --nodeps"`.
              When update_cache, force redownload repo databases.
              Same as `update_cache_extra_args="--refresh --refresh"`.
        default: no
        type: bool
        version_added: "2.0"

    extra_args:
        description:
            - Additional option to pass to yay when enforcing C(state).
        default:
        version_added: "2.8"

    update_cache:
        description:
            - Whether or not to refresh the master package lists.
            - This can be run as part of a package installation or as a separate step.
        default: no
        type: bool
        aliases: [ update-cache ]

    update_cache_extra_args:
        description:
            - Additional option to pass to yay when enforcing C(update_cache).
        default:
        version_added: "2.8"

    upgrade:
        description:
            - Whether or not to upgrade the whole system.
              Can't be used in combination with C(name).
        default: no
        type: bool
        version_added: "2.0"

    upgrade_extra_args:
        description:
            - Additional option to pass to yay when enforcing C(upgrade).
        default:
        version_added: "2.8"

notes:
  - When used with a `loop:` each package will be processed individually,
    it is much more efficient to pass the list directly to the `name` option.
'''

RETURN = '''
packages:
    description: a list of packages that have been changed
    returned: when upgrade is set to yes
    type: list
    sample: [ package, other-package ]
'''

EXAMPLES = '''
- name: Install package foo from repo
  yay:
    name: foo
    state: present

- name: Install package bar from file
  yay:
    name: ~/bar-1.0-1-any.pkg.tar.xz
    state: present

- name: Install package foo from repo and bar from file
  yay:
    name:
      - foo
      - ~/bar-1.0-1-any.pkg.tar.xz
    state: present

- name: Upgrade package foo
  yay:
    name: foo
    state: latest
    update_cache: yes

- name: Remove packages foo and bar
  yay:
    name:
      - foo
      - bar
    state: absent

- name: Recursively remove package baz
  yay:
    name: baz
    state: absent
    extra_args: --recursive

- name: Run the equivalent of "yay -Sy" as a separate step
  yay:
    update_cache: yes

- name: Run the equivalent of "yay -Su" as a separate step
  yay:
    upgrade: yes

- name: Run the equivalent of "yay -Syu" as a separate step
  yay:
    update_cache: yes
    upgrade: yes

- name: Run the equivalent of "yay -Rdd", force remove package baz
  yay:
    name: baz
    state: absent
    force: yes
'''

import re

from ansible.module_utils.basic import AnsibleModule


def get_version(yay_output):
    """Take yay -Qi or yay -Si output and get the Version"""
    lines = yay_output.split('\n')
    for line in lines:
        if 'Version' in line:
            return line.split(':')[1].strip()
    return None


def query_package(module, yay_path, name, state="present"):
    """Query the package status in both the local system and the repository. Returns a boolean to indicate if the package is installed, a second
    boolean to indicate if the package is up-to-date and a third boolean to indicate whether online information were available
    """
    if state == "present":
        lcmd = "%s --query --info %s" % (yay_path, name)
        lrc, lstdout, lstderr = module.run_command(lcmd, check_rc=False)
        if lrc != 0:
            # package is not installed locally
            return False, False, False

        # get the version installed locally (if any)
        lversion = get_version(lstdout)

        rcmd = "%s --sync --info %s" % (yay_path, name)
        rrc, rstdout, rstderr = module.run_command(rcmd, check_rc=False)
        # get the version in the repository
        rversion = get_version(rstdout)

        if rrc == 0:
            # Return True to indicate that the package is installed locally, and the result of the version number comparison
            # to determine if the package is up-to-date.
            return True, (lversion == rversion), False

    # package is installed but cannot fetch remote Version. Last True stands for the error
        return True, True, True


def update_package_db(module, yay_path):
    if module.params['force']:
        module.params["update_cache_extra_args"] += " --refresh --refresh"

    cmd = "%s --sync --refresh %s" % (yay_path, module.params["update_cache_extra_args"])
    rc, stdout, stderr = module.run_command(cmd, check_rc=False)

    if rc == 0:
        return True
    else:
        module.fail_json(msg="could not update package db")


def upgrade(module, yay_path):
    cmdupgrade = "%s --sync --sysupgrade --quiet --noconfirm %s" % (yay_path, module.params["upgrade_extra_args"])
    cmdneedrefresh = "%s --query --upgrades" % (yay_path)
    rc, stdout, stderr = module.run_command(cmdneedrefresh, check_rc=False)
    data = stdout.split('\n')
    data.remove('')
    packages = []
    diff = {
        'before': '',
        'after': '',
    }

    if rc == 0:
        regex = re.compile(r'([\w-]+) ((?:\S+)-(?:\S+)) -> ((?:\S+)-(?:\S+))')
        for p in data:
            m = regex.search(p)
            packages.append(m.group(1))
            if module._diff:
                diff['before'] += "%s-%s\n" % (m.group(1), m.group(2))
                diff['after'] += "%s-%s\n" % (m.group(1), m.group(3))
        if module.check_mode:
            module.exit_json(changed=True, msg="%s package(s) would be upgraded" % (len(data)), packages=packages, diff=diff)
        rc, stdout, stderr = module.run_command(cmdupgrade, check_rc=False)
        if rc == 0:
            module.exit_json(changed=True, msg='System upgraded', packages=packages, diff=diff)
        else:
            module.fail_json(msg="Could not upgrade")
    else:
        module.exit_json(changed=False, msg='Nothing to upgrade', packages=packages)


def remove_packages(module, yay_path, packages):
    data = []
    diff = {
        'before': '',
        'after': '',
    }

    if module.params["force"]:
        module.params["extra_args"] += " --nodeps --nodeps"

    remove_c = 0
    # Using a for loop in case of error, we can report the package that failed
    for package in packages:
        # Query the package first, to see if we even need to remove
        installed, updated, unknown = query_package(module, yay_path, package)
        if not installed:
            continue

        cmd = "%s --remove --noconfirm --noprogressbar %s %s" % (yay_path, module.params["extra_args"], package)
        rc, stdout, stderr = module.run_command(cmd, check_rc=False)

        if rc != 0:
            module.fail_json(msg="failed to remove %s" % (package))

        if module._diff:
            d = stdout.split('\n')[2].split(' ')[2:]
            for i, pkg in enumerate(d):
                d[i] = re.sub('-[0-9].*$', '', d[i].split('/')[-1])
                diff['before'] += "%s\n" % pkg
            data.append('\n'.join(d))

        remove_c += 1

    if remove_c > 0:
        module.exit_json(changed=True, msg="removed %s package(s)" % remove_c, diff=diff)

    module.exit_json(changed=False, msg="package(s) already absent")


def install_packages(module, yay_path, state, packages, package_files):
    install_c = 0
    package_err = []
    message = ""
    data = []
    diff = {
        'before': '',
        'after': '',
    }

    to_install_repos = []
    to_install_files = []
    for i, package in enumerate(packages):
        # if the package is installed and state == present or state == latest and is up-to-date then skip
        installed, updated, latestError = query_package(module, yay_path, package)
        if latestError and state == 'latest':
            package_err.append(package)

        if installed and (state == 'present' or (state == 'latest' and updated)):
            continue

        if package_files[i]:
            to_install_files.append(package_files[i])
        else:
            to_install_repos.append(package)

    if to_install_repos:
        cmd = "%s --sync --noconfirm --noprogressbar --needed %s %s" % (yay_path, module.params["extra_args"], " ".join(to_install_repos))
        rc, stdout, stderr = module.run_command(cmd, check_rc=False)

        if rc != 0:
            module.fail_json(msg="failed to install %s: %s" % (" ".join(to_install_repos), stderr))

        data = stdout.split('\n')[3].split(' ')[2:]
        data = [i for i in data if i != '']
        for i, pkg in enumerate(data):
            data[i] = re.sub('-[0-9].*$', '', data[i].split('/')[-1])
            if module._diff:
                diff['after'] += "%s\n" % pkg

        install_c += len(to_install_repos)

    if to_install_files:
        cmd = "%s --upgrade --noconfirm --noprogressbar --needed %s %s" % (yay_path, module.params["extra_args"], " ".join(to_install_files))
        rc, stdout, stderr = module.run_command(cmd, check_rc=False)

        if rc != 0:
            module.fail_json(msg="failed to install %s: %s" % (" ".join(to_install_files), stderr))

        data = stdout.split('\n')[3].split(' ')[2:]
        data = [i for i in data if i != '']
        for i, pkg in enumerate(data):
            data[i] = re.sub('-[0-9].*$', '', data[i].split('/')[-1])
            if module._diff:
                diff['after'] += "%s\n" % pkg

        install_c += len(to_install_files)

    if state == 'latest' and len(package_err) > 0:
        message = "But could not ensure 'latest' state for %s package(s) as remote version could not be fetched." % (package_err)

    if install_c > 0:
        module.exit_json(changed=True, msg="installed %s package(s). %s" % (install_c, message), diff=diff)

    module.exit_json(changed=False, msg="package(s) already installed. %s" % (message), diff=diff)


def check_packages(module, yay_path, packages, state):
    would_be_changed = []
    diff = {
        'before': '',
        'after': '',
        'before_header': '',
        'after_header': ''
    }

    for package in packages:
        installed, updated, unknown = query_package(module, yay_path, package)
        if ((state in ["present", "latest"] and not installed) or
                (state == "absent" and installed) or
                (state == "latest" and not updated)):
            would_be_changed.append(package)
    if would_be_changed:
        if state == "absent":
            state = "removed"

        if module._diff and (state == 'removed'):
            diff['before_header'] = 'removed'
            diff['before'] = '\n'.join(would_be_changed) + '\n'
        elif module._diff and ((state == 'present') or (state == 'latest')):
            diff['after_header'] = 'installed'
            diff['after'] = '\n'.join(would_be_changed) + '\n'

        module.exit_json(changed=True, msg="%s package(s) would be %s" % (
            len(would_be_changed), state), diff=diff)
    else:
        module.exit_json(changed=False, msg="package(s) already %s" % state, diff=diff)


def expand_package_groups(module, yay_path, pkgs):
    expanded = []

    for pkg in pkgs:
        if pkg:  # avoid empty strings
            cmd = "%s --sync --groups --quiet %s" % (yay_path, pkg)
            rc, stdout, stderr = module.run_command(cmd, check_rc=False)

            if rc == 0:
                # A group was found matching the name, so expand it
                for name in stdout.split('\n'):
                    name = name.strip()
                    if name:
                        expanded.append(name)
            else:
                expanded.append(pkg)

    return expanded


def main():
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type='list', aliases=['pkg', 'package']),
            state=dict(type='str', default='present', choices=['present', 'installed', 'latest', 'absent', 'removed']),
            recurse=dict(type='bool', default=False),
            force=dict(type='bool', default=False),
            extra_args=dict(type='str', default=''),
            upgrade=dict(type='bool', default=False),
            upgrade_extra_args=dict(type='str', default=''),
            update_cache=dict(type='bool', default=False, aliases=['update-cache']),
            update_cache_extra_args=dict(type='str', default=''),
        ),
        required_one_of=[['name', 'update_cache', 'upgrade']],
        mutually_exclusive=[['name', 'upgrade']],
        supports_check_mode=True,
    )

    yay_path = module.get_bin_path('yay', True)

    p = module.params

    # normalize the state parameter
    if p['state'] in ['present', 'installed']:
        p['state'] = 'present'
    elif p['state'] in ['absent', 'removed']:
        p['state'] = 'absent'

    if p['recurse']:
        module.deprecate('Option `recurse` is deprecated and will be removed in '
                         'version 2.10. Please use `extra_args=--recursive` '
                         'instead', '2.10')
        if p['state'] == 'absent':
            p['extra_args'] += " --recursive"

    if p["update_cache"] and not module.check_mode:
        update_package_db(module, yay_path)
        if not (p['name'] or p['upgrade']):
            module.exit_json(changed=True, msg='Updated the package master lists')

    if p['update_cache'] and module.check_mode and not (p['name'] or p['upgrade']):
        module.exit_json(changed=True, msg='Would have updated the package cache')

    if p['upgrade']:
        upgrade(module, yay_path)

    if p['name']:
        pkgs = expand_package_groups(module, yay_path, p['name'])

        pkg_files = []
        for i, pkg in enumerate(pkgs):
            if not pkg:  # avoid empty strings
                continue
            elif re.match(r".*\.pkg\.tar(\.(gz|bz2|xz|lrz|lzo|Z))?$", pkg):
                # The package given is a filename, extract the raw pkg name from
                # it and store the filename
                pkg_files.append(pkg)
                pkgs[i] = re.sub(r'-[0-9].*$', '', pkgs[i].split('/')[-1])
            else:
                pkg_files.append(None)

        if module.check_mode:
            check_packages(module, yay_path, pkgs, p['state'])

        if p['state'] in ['present', 'latest']:
            install_packages(module, yay_path, p['state'], pkgs, pkg_files)
        elif p['state'] == 'absent':
            remove_packages(module, yay_path, pkgs)
    else:
        module.exit_json(changed=False, msg="No package specified to work on.")


if __name__ == "__main__":
    main()
