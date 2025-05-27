
[TUTORPAGE](https://telegra.ph/Create-Telegram-Mirror-Leech-Bot-by-Deploying-App-with-Heroku-Branch-using-Github-Workflow-12-06)




# [Agree to acces credentials api](https://console.developers.google.com/apis/credentials)

# [ENABLE API LIBRARY](https://console.cloud.google.com/apis/library)

```
Google Drive API
```
```
Identity and Access Management (IAM) API
```

# [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
-> External -> Create

- credentials.json
Create OAuth client ID - desktop - credentials.json

# Install requirements
```
pip install -r requirements-cli.txt
```

# Generate token.pickle
```
python3 generate_drive_token.py
```

## Generate SAccount
Edit gen_sa_accounts._create_sa_keys.enumerate.start=(start_no) for filename.json

- generate SA in existing project
```
python3 gen_sa_accounts.py -p acc@gmail.com --quick-setup -1 --parse-mail
```

- create new project and generate SA
```
python3 gen_sa_accounts.py -p acc@gmail.com --quick-setup 12 --new-only --parse-mail
```

- parse-mail
```
python3 gen_sa_accounts.py -p acc@gmail.com --parse-mail
```

## Add Service Account to SHARED DRIVE / TEAM DRIVE / GOOGLE GROUP
Note: if you are using --parse-mail, your mail is alredy parse.

- windows 
```
$emails = Get-ChildItem .\**.json |Get-Content -Raw |ConvertFrom-Json |Select -ExpandProperty client_email >>emails.txt
```

- linux
```
grep -oPh '"client_email": "\K[^"]+' accounts/*.json > mail.txt
```

# [Add to google group](https://groups.google.com/)

# remove duplicate
```
cat mail.txt added.txt | sort | uniq -u > to_add.txt
```




