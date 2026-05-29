# RxTrack Cloud Run Deployment

This keeps the current Streamlit deployment usable while creating a separate Cloud Run copy.

## 1. Create or select a Google Cloud project

```powershell
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com
```

## 2. Store secrets

Create the Neon database secret:

```powershell
gcloud secrets create neon-db-url --replication-policy="automatic"
gcloud secrets versions add neon-db-url --data-file=-
```

Paste the Neon database URL, then press `Ctrl+Z` and Enter in PowerShell.

Optional management password. Skip this if you want to use the app's current fallback password while testing:

```powershell
gcloud secrets create management-password --replication-policy="automatic"
gcloud secrets versions add management-password --data-file=-
```

Recommended app-wide password for public Cloud Run URLs:

```powershell
gcloud secrets create rxtrack-app-password --replication-policy="automatic"
gcloud secrets versions add rxtrack-app-password --data-file=-
```

Paste the app password, then press `Ctrl+Z` and Enter in PowerShell.

## 3. Deploy a test copy first

From the repo folder:

```powershell
gcloud run deploy rxtrack-test --source . --region us-central1 --allow-unauthenticated --memory 2Gi --cpu 1 --timeout 3600 --set-secrets NEON_DB_URL=neon-db-url:latest
```

Recommended password-protected test deploy:

```powershell
gcloud run deploy rxtrack-test --source . --region us-central1 --allow-unauthenticated --memory 2Gi --cpu 1 --timeout 3600 --set-secrets NEON_DB_URL=neon-db-url:latest,APP_PASSWORD=rxtrack-app-password:latest
```

If you created the optional management password secret, use:

```powershell
gcloud run deploy rxtrack-test --source . --region us-central1 --allow-unauthenticated --memory 2Gi --cpu 1 --timeout 3600 --set-secrets NEON_DB_URL=neon-db-url:latest,MANAGEMENT_PASSWORD=management-password:latest
```

Use `rxtrack-test` side-by-side with the current work app until uploads, pages, and barcode scanning feel reliable.

## 4. Promote later

Only after the test copy is boring and reliable:

```powershell
gcloud run deploy rxtrack --source . --region us-central1 --allow-unauthenticated --memory 2Gi --cpu 1 --timeout 3600 --set-secrets NEON_DB_URL=neon-db-url:latest
```

Recommended password-protected production deploy:

```powershell
gcloud run deploy rxtrack --source . --region us-central1 --allow-unauthenticated --memory 2Gi --cpu 1 --timeout 3600 --set-secrets NEON_DB_URL=neon-db-url:latest,APP_PASSWORD=rxtrack-app-password:latest
```

Or with the optional management password secret:

```powershell
gcloud run deploy rxtrack --source . --region us-central1 --allow-unauthenticated --memory 2Gi --cpu 1 --timeout 3600 --set-secrets NEON_DB_URL=neon-db-url:latest,MANAGEMENT_PASSWORD=management-password:latest
```

Keep the old deployment available as backup until Cloud Run has been stable for a while.
