

from google.oauth2 import service_account
from googleapiclient.discovery import build


def get_readable_file_size(size_in_bytes):
    if not size_in_bytes:
        return "0B"

    SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while size_in_bytes >= 1024 and index < len(SIZE_UNITS) - 1:
        size_in_bytes /= 1024
        index += 1

    return f"{size_in_bytes:.2f}{SIZE_UNITS[index]}"


def build_service(sa_path):
    SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly']
    creds = service_account.Credentials.from_service_account_file(
        sa_path, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds), creds


def get_all_files_by(SA_PATH):
    service, creds = build_service(SA_PATH)
    client_email = creds.service_account_email
    results = service.files().list(
        q=f"'{client_email}' in owners",
        pageSize=100,
        fields="files(id, name, size, webViewLink)"
    ).execute()
    return results.get('files', [])


def main():
    # input("SA_credentials.json path: ")
    SA_PATH = "acc@gmail.com/accounts/0.json"
    items = get_all_files_by(SA_PATH)

    total_size = 0
    for i, file in enumerate(items, start=1):
        size = int(file.get('size', 0))
        total_size += size
        print(f"{i}. {
            get_readable_file_size(size)
        } | {
            file['name']
        } | {
            file['webViewLink']
        }"
        )
    print("Total: ", get_readable_file_size(total_size))


if __name__ == "__main__":
    main()
