terraform {
  # Using local backend for testing
  # For production, use S3 backend with backend-config file
  backend "local" {
    path = "terraform.tfstate"
  }
}
