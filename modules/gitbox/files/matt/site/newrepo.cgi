#!/usr/bin/env python
#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# This is newrepo.cgi - script for self-serve new github/gitbox repos

import hashlib, json, random, os, sys, time, subprocess, re, ldap
import cgi, sqlite3, hashlib, Cookie, urllib, urllib2, ConfigParser
import requests
import smtplib
from email.mime.text import MIMEText

# LDAP settings
CONFIG = ConfigParser.ConfigParser()
CONFIG.read("/x1/gitbox/matt/tools/grouper.cfg")

LDAP_URI = "ldaps://ldap-lb-us.apache.org:636"
LDAP_USER = CONFIG.get('ldap', 'user')
LDAP_PASSWORD = CONFIG.get('ldap', 'password')
UID_RE = re.compile("uid=([^,]+),ou=people,dc=apache,dc=org")
ORG_READ_TOKEN = CONFIG.get('github', 'token')
HIPCHAT_TOKEN = CONFIG.get('hipchat', 'token')

# Hardcoded repo limits
PMCS = ['infrastructure', 'couchdb', 'trafficserver', 'whimsy', 'tika', 'nutch', 'openwhisk', 'cloudstack']

# CGI
xform = cgi.FieldStorage();

""" Get a POST/GET value """
def getvalue(key):
    val = xform.getvalue(key)
    if val:
        return val
    else:
        return None
    
    
""" Get LDAP groups a user belongs to """
def ldap_groups(uid):
    ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)
    l = ldap.initialize(LDAP_URI)
    # this search for all objectClasses that user is in.
    # change this to suit your LDAP schema
    search_filter= "(|(member=%s)(member=uid=%s,ou=people,dc=apache,dc=org))" % (uid, uid)
    try:
        groups = []
        LDAP_BASE = "ou=groups,dc=apache,dc=org"
        results = l.search_s(LDAP_BASE, ldap.SCOPE_SUBTREE, search_filter, ['cn',])
        for res in results:
            groups.append(res[1]['cn'][0]) # each res is a tuple: ('cn=full,ou=ldap,dc=uri', {'cn': ['tlpname']})
        infra = getStandardGroup('infrastructure', 'cn=infrastructure,ou=groups,ou=services,dc=apache,dc=org')
        if infra and uid in infra:
            groups.append('infrastructure')
        return groups
    except Exception as err:
        pass
    return []

def getStandardGroup(group, ldap_base = None):
    """ Gets the list of availids in a standard group (pmcs, services, podlings) """
    # First, check if there's a hardcoded member list for this group
    # If so, read it and return that instead of trying LDAP
    if CONFIG.has_section('group:%s' % group) and CONFIG.has_option('group:%s' % group, 'members'):
        return CONFIG.get('group:%s' % group, 'members').split(' ')
    groupmembers = []
    # This might fail in case of ldap bork, if so we'll return nothing.
    try:
        ldapClient = ldap.initialize(LDAP_URI)
        ldapClient.set_option(ldap.OPT_REFERRALS, 0)

        ldapClient.bind(LDAP_USER, LDAP_PASSWORD)
        
        # Default LDAP base if not specified
        if not ldap_base:
            ldap_base = "cn=%s,ou=project,ou=groups,dc=apache,dc=org" % group

        # This is using the new podling/etc LDAP groups defined by Sam
        results = ldapClient.search_s(ldap_base, ldap.SCOPE_BASE)

        for result in results:
            result_dn = result[0]
            result_attrs = result[1]
            # We are only interested in the member attribs here. owner == ppmc, but we don't care
            if "member" in result_attrs:
                for member in result_attrs["member"]:
                    m = UID_RE.match(member) # results are in the form uid=janedoe,dc=... so weed out the uid
                    if m:
                        groupmembers.append(m.group(1))

        ldapClient.unbind_s()
        groupmembers = sorted(groupmembers) #alphasort
    except Exception as err:
        print(err)
        groupmembers = None
    return groupmembers


def createRepo(repo, title, pmc):
    url = "https://api.github.com/orgs/apache/repos"
    r = requests.post(url, data = json.dumps({
            'name': repo,
            'description': title,
            'homepage': "https://%s.apache.org/" % pmc,
            'private': False,
            'has_issues': False,
            'has_projects': False,
            'has_wiki': False
            }),
            headers = {'Authorization': "token %s" % ORG_READ_TOKEN})
    
    #201 == New repo created
    if r.status_code == 201:
        return True
    # Otherwise, exists already or other error?
    else:
        print("Status: 200 Okay\r\nContent-Type: application/json\r\n\r\n")
        print(json.dumps({
            'created': False,
            'error': r.text
        }))
        return False

def hipchat(msg):
    payload = {
            'room_id': "669587",
            'auth_token': HIPCHAT_TOKEN,
            'from': "GitBox RepoReq",
            'message_format': 'html',
            'notify': '0',
            'color':'green',
            'message': msg
        }
    requests.post('https://api.hipchat.com/v1/rooms/message', data = payload)
    
def main():
    action = xform.getvalue("action")
    if action and action == "create":
        
        # Check if allowed to create
        pmc = xform.getvalue("pmc")
        if pmc in PMCS:
            
            # Repo name and title
            repo = xform.getvalue("name")
            reponame = pmc
            title = "Apache %s" % pmc
            if repo:
                reponame = "%s-%s" % (pmc, repo)
                title = "Apache %s %s" % (pmc, repo)
            t = xform.getvalue("description")
            if t:
                title = t
                
            # Email settings
            commitmail = "commits@%s.apache.org" % pmc
            ghmail = "dev@%s.apache.org" % pmc
            cf = xform.getvalue("notify")
            gf = xform.getvalue("ghnotify")
            if cf:
                commitmail = cf
            if gf:
                ghmail = gf
            
            # clean up variables
            reponame = re.sub(r"[^-a-zA-Z0-9]+", "", reponame)
            title = re.sub(r"[^-a-zA-Z0-9 .,]+", "", title)
            commitmail = re.sub(r"[^-a-zA-Z0-9@]+", "", commitmail)
            ghmail = re.sub(r"[^-a-zA-Z0-9@]+", "", ghmail)
            
            created = createRepo(reponame, title, pmc)
            if created:
                try:
                    # Clone repo
                    subprocess.check_output("cd /x1/repos/asf/ && /x1/gitbox/bin/gitbox-clone -c %s -d \"%s\" https://github.com/apache/%s.git %s.git" % (commitmail, title, reponame, reponame), shell = True)
                    time.sleep(3) # Wait for GH??
                    # Set apache.dev value in config
                    subprocess.check_output("cd /x1/repos/asf/%s.git/ && git config apache.dev \"%s\"" % (reponame, ghmail), shell = True)
                    
                    # Notify infra@ and private@$pmc that the repo has been set up
                    hipchat("New repository request for %s.git by %s succeeded!" % (reponame, os.environ['REMOTE_USER']))
                    msg = MIMEText("New repository %s.git was created, as requested by %s.\nYou may view it at: https://gitbox.apache.org/repos/asf/%s.git\n\nWith regards,\nApache Infrastructure." % (reponame, os.environ['REMOTE_USER'], reponame))
                    msg['Subject'] = 'New gitbox/github repository created: %s.git' % reponame
                    msg['From'] = "git@apache.org"
                    msg['Reply-To'] = "users@infra.apache.org"
                    if pmc == 'infrastructure':
                        pmc = 'infra' # hack hack hack
                    msg['To'] = "users@infra.apache.org, private@%s.apache.org" % pmc
                    
                    s = smtplib.SMTP(host='mail.apache.org', port=2025)
                    s.sendmail("private@infra.apache.org", "private@%s.apache.org" % pmc, msg.as_string())
                    s.quit()

                    
                except subprocess.CalledProcessError as e:
                    print("Status: 500 NOT Okay\r\nContent-Type: application/json\r\n\r\n")
                    print(json.dumps({
                        'created': False,
                        'error': e['message']
                    }))
                    
                    
                    # Notify infra@ about this!
                    msg = MIMEText("New repository %s.git creation requested by %s FAILED: \n\n%s" % (reponame, os.environ['REMOTE_USER'], e['message']))
                    msg['Subject'] = 'New gitbox/github repository failed: %s.git' % reponame
                    msg['From'] = "git@apache.org"
                    msg['Reply-To'] = "private@infra.apache.org"
                    msg['To'] = "private@infra.apache.org"
                    s = smtplib.SMTP(host='mail.apache.org', port=2025)
                    s.sendmail("private@infra.apache.org", msg.as_string())
                    s.quit()
                    
                    hipchat("New repository request for %s.git by %s failed! Check yer inbox for details." % (reponame, os.environ['REMOTE_USER']))
                    return
            else:
                return
            print("Status: 200 Okay\r\nContent-Type: application/json\r\n\r\n")
            print(json.dumps({
                'created': created
            }))
            return
    if action and action == "pmcs":
        groups = ldap_groups(os.environ['REMOTE_USER'])
        groups = [group for group in groups if group in PMCS]
        print("Status: 200 Okay\r\nContent-Type: application/json\r\n\r\n")
        print(json.dumps({
            'pmcs': groups
        }))
        return
    
    print("Status: 200 Okay\r\nContent-Type: application/json\r\n\r\n")
    print(json.dumps({
        'failed': True
    }))


if __name__ == '__main__':
    main()
