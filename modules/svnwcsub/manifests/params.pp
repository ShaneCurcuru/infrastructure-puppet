class svnwcsub::params {
    $uid            = 9997
    $gid            = 9997
    $conf_path      = '/etc'
    $conf_file      = 'svnwcsub.conf'
    $groupname      = 'svnwc'
    $groups         = ['']
    $service_ensure = 'running'
    $service_name   = 'svnwc'
    $shell          = '/bin/bash'
    $username       = 'svnwc'
}
