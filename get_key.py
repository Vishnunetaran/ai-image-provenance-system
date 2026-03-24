import requests
r = requests.post('http://127.0.0.1:5000/api/v1/keys', json={'org_name': 'FinalTry'})
print(f'[{r.json()["plaintext_key"]}]')
