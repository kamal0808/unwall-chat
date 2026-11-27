from extract import redact, visible
J = '.'.join(['eyJ' + 'madeupheader', 'eyJ' + 'madeupbody', 'abc123'])  # fake, built so secret scanners skip it
for s in ['sk-ant-api03-AbCdEf123456789', 'ghp_' + 'abcdefghij' * 2, J, 'password: hunter2xyz', 'the API key is `zzz999yyy`',
          'postgres://u:p4ssw0rd@host/db', 'Authorization: Bearer abcDEF1234567890xyz', 'AIzaSyA1234567890abcdefghij',
          'Qx7' * 12, 'a1' * 30, 'id 8aa9ae0123456789abcdef0123456789', 'V2Vr7eAb12cdEF34ghIJ56', 'tok Ab1-cd2Ef3-gh4Ij5Kl6-mn7', 'OTP: 482913']:
    assert '[redacted]' in redact(s), s
for s in ['Open /Users/kamal/dev/topc/ecosystem/TOPC.md', 'the token budget grew', 'session local_00000000-aaaa-4bbb-8ccc-000000000000', 'x.com/2-BHK-650-Sq-ft-Office-Space-Rent-Pune']:
    assert redact(s) == s, (s, redact(s))
u = lambda c, **k: {'type': 'user', 'message': {'content': c}, **k}
H = {'origin': {'kind': 'human'}}
assert visible(u('<system-reminder>x</system-reminder>\nhi', **H)) == ('k', 'hi')
assert visible(u('hi')) is None and visible(u('hi', origin={'kind': 'peer'})) is None
assert visible(u([{'type': 'tool_result', 'content': 'out'}], **H)) is None
assert visible({'type': 'assistant', 'message': {'content': [{'type': 'thinking', 'thinking': 'x'}, {'type': 'tool_use'}]}}) is None
print('check ok')
