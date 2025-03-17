# AI Resume and Cover Letter Generator

An intelligent web application that automatically generates tailored resumes and cover letters based on your profile and job descriptions.

## Features

- **User Profiles**: Create and manage multiple user profiles with resumes, skills, and professional details
- **AI-Powered Document Generation**: Uses OpenAI to create custom-tailored resumes and cover letters
- **Job Description Analysis**: Automatically extracts company names and analyzes job requirements
- **Multiple File Formats**: Supports viewing documents as HTML and saving as PDF
- **Skills Extraction**: Automatically extracts and categorizes your professional skills
- **Real-time Progress**: View generation logs in real-time

## Screenshot

<!-- Add a screenshot of your application here -->
<!-- ![Resume Generator](static/img/screenshot.png) -->
*Note: Replace with your own screenshot after deployment*

## Getting Started

### Prerequisites

- Python 3.8 or higher
- An OpenAI API key

### Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/resume-cover-letter-generator.git
   cd resume-cover-letter-generator
   ```

2. Create and activate a virtual environment:
   ```
   python -m venv venv
   ```
   
   On Windows:
   ```
   .\venv\Scripts\activate
   ```
   
   On macOS/Linux:
   ```
   source venv/bin/activate
   ```

3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

4. Create a `.env` file in the project root and add your configuration (see `.env.example`):
   ```
   OPENAI_API_KEY=your_openai_api_key
   OPENAI_MODEL=gpt-4
   SECRET_KEY=your_flask_secret_key
   ```

5. Start the application:
   ```
   python app.py
   ```

6. Open your browser and navigate to `http://localhost:5000`

## Usage

1. **Create a Profile**: Upload your resume and provide basic information
2. **Enter Job Description**: Paste a job description or upload a job posting file
3. **Generate Documents**: Click the "Generate Documents" button to create tailored documents
4. **View & Download**: View the generated HTML files and save as PDF

## Project Structure

```
.
├── app.py                   # Main Flask application
├── templates/               # HTML templates
├── static/                  # CSS, JavaScript, and images
├── uploads/                 # Uploaded resume and job files
├── generated/               # Generated documents
├── user_profiles/           # Stored user profiles
├── requirements.txt         # Python dependencies
└── .env                     # Environment variables
```

## Troubleshooting

- **API Key Issues**: Ensure your OpenAI API key is correctly set in the `.env` file
- **Document Generation Errors**: Check the logs section for detailed error messages
- **PDF Generation**: If PDF download isn't working, try using the "Print" function in your browser

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Uses OpenAI GPT models for content generation
- Built with Flask, a lightweight WSGI web application framework
- PyMuPDF for PDF text extraction

---

*Note: This project uses the OpenAI API, which has usage costs associated with it. Please check OpenAI's pricing page to understand the cost implications before extensive use.* 