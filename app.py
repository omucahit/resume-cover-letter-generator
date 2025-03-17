import os
import json
import re
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from io import StringIO

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from werkzeug.utils import secure_filename
import openai
import fitz  # PyMuPDF
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Create a string buffer for capturing logs to display in the web UI
log_capture_string = StringIO()
log_handler = logging.StreamHandler(log_capture_string)
log_handler.setLevel(logging.INFO)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(log_handler)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'txt', 'docx'}

# Add context processor for templates
@app.context_processor
def utility_processor():
    return {
        'now': datetime.now
    }

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Create output folder for generated files
OUTPUT_FOLDER = 'generated'
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Create temp folder for HTML files
TEMP_FOLDER = 'temp'
os.makedirs(TEMP_FOLDER, exist_ok=True)

# Create user profiles folder
USER_PROFILES_FOLDER = 'user_profiles'
os.makedirs(USER_PROFILES_FOLDER, exist_ok=True)

class UserProfile:
    def __init__(self, first_name="", last_name=""):
        self.first_name = first_name
        self.last_name = last_name
        self.resume_text = ""
        self.portfolio_text = ""
        self.linkedin_text = ""
        self.skills = []
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.updated_at = self.created_at
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
    
    @property
    def folder_name(self):
        if not self.full_name:
            return None
        return f"{self.first_name}_{self.last_name}".replace(" ", "_")
    
    def to_dict(self):
        return {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "resume_text": self.resume_text,
            "portfolio_text": self.portfolio_text,
            "linkedin_text": self.linkedin_text,
            "skills": self.skills,
            "created_at": self.created_at,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    
    @classmethod
    def from_dict(cls, data):
        profile = cls()
        profile.first_name = data.get("first_name", "")
        profile.last_name = data.get("last_name", "")
        profile.resume_text = data.get("resume_text", "")
        profile.portfolio_text = data.get("portfolio_text", "")
        profile.linkedin_text = data.get("linkedin_text", "")
        profile.skills = data.get("skills", [])
        profile.created_at = data.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        profile.updated_at = data.get("updated_at", profile.created_at)
        return profile
    
    def save(self):
        if not self.folder_name:
            raise ValueError("User profile must have a name before saving")
        
        folder_path = os.path.join(USER_PROFILES_FOLDER, self.folder_name)
        os.makedirs(folder_path, exist_ok=True)
        
        file_path = os.path.join(folder_path, "profile.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2)
        
        logger.info(f"Saved user profile for {self.full_name}")
        return folder_path
    
    @classmethod
    def load(cls, folder_name):
        file_path = os.path.join(USER_PROFILES_FOLDER, folder_name, "profile.json")
        if not os.path.exists(file_path):
            logger.error(f"User profile not found: {folder_name}")
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            profile = cls.from_dict(data)
            logger.info(f"Loaded user profile for {profile.full_name}")
            return profile
        except Exception as e:
            logger.error(f"Error loading user profile: {str(e)}")
            return None
    
    @classmethod
    def get_all_profiles(cls):
        profiles = []
        if os.path.exists(USER_PROFILES_FOLDER):
            for folder_name in os.listdir(USER_PROFILES_FOLDER):
                folder_path = os.path.join(USER_PROFILES_FOLDER, folder_name)
                if os.path.isdir(folder_path):
                    profile_path = os.path.join(folder_path, "profile.json")
                    if os.path.exists(profile_path):
                        try:
                            with open(profile_path, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                            profile = cls.from_dict(data)
                            profiles.append(profile)
                        except Exception as e:
                            logger.error(f"Error loading profile {folder_name}: {str(e)}")
        
        # Sort by updated_at (newest first)
        profiles.sort(key=lambda p: p.updated_at, reverse=True)
        return profiles

class ResumeAndCoverLetterGenerator:
    def __init__(self):
        self.resume_texts = []  # List to store multiple resume texts
        self.job_description = ""
        self.company_name = ""
        self.style_attributes = {}
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4")
        self.current_user_profile = None
        
        # Initialize OpenAI client if API key is available
        if self.api_key:
            openai.api_key = self.api_key
        else:
            logger.warning("OpenAI API key not found in environment variables.")
            logger.warning("Please set your OPENAI_API_KEY environment variable or create a .env file.")
    
    def set_user_profile(self, profile):
        """Set the current user profile"""
        self.current_user_profile = profile
        if profile and profile.resume_text:
            self.resume_texts = [profile.resume_text]
            logger.info(f"Loaded resume from user profile: {profile.full_name}")
        return self.current_user_profile
    
    def extract_user_info(self, resume_text, first_name="", last_name="", portfolio_text="", linkedin_text=""):
        """Extract user information from resume, portfolio, and LinkedIn data"""
        try:
            logger.info("Creating user profile")
            
            # Create and return a new user profile
            profile = UserProfile(
                first_name=first_name,
                last_name=last_name
            )
            profile.resume_text = resume_text
            profile.portfolio_text = portfolio_text
            profile.linkedin_text = linkedin_text
            
            # Extract skills
            profile.skills = self.extract_skills(profile)
            
            return profile
            
        except Exception as e:
            logger.error(f"Error creating user profile: {e}")
            # Create a profile with default values
            profile = UserProfile(first_name=first_name, last_name=last_name)
            profile.resume_text = resume_text
            profile.portfolio_text = portfolio_text
            profile.linkedin_text = linkedin_text
            return profile
    
    def extract_skills(self, profile):
        """Extract skills from user profile data"""
        try:
            logger.info("Extracting skills from user profile data")
            
            combined_text = f"""
            Resume:
            {profile.resume_text}
            
            Portfolio:
            {profile.portfolio_text if profile.portfolio_text else "Not provided"}
            
            LinkedIn:
            {profile.linkedin_text if profile.linkedin_text else "Not provided"}
            """
            
            prompt = f"""
            Extract a comprehensive list of professional skills from the following user data.
            Include technical skills, soft skills, tools, technologies, and domain knowledge.
            Return the skills as a JSON array of strings, with each skill being specific and concise.
            Format your response as a valid JSON object with a single key "skills" containing the array.
            
            {combined_text}
            """
            
            client = openai.OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You extract professional skills from user data. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.3
            )
            
            # Parse JSON from text response
            response_text = response.choices[0].message.content.strip()
            try:
                result = json.loads(response_text)
                skills = result.get("skills", [])
                logger.info(f"Extracted {len(skills)} skills from user profile")
                return skills
            except json.JSONDecodeError:
                # Fallback: try to extract skills using regex if JSON parsing fails
                logger.warning("Failed to parse JSON response, attempting to extract skills with regex")
                import re
                # Look for anything that might be a skill (words or phrases in quotes)
                skills_match = re.findall(r'"([^"]+)"', response_text)
                if skills_match:
                    logger.info(f"Extracted {len(skills_match)} skills using regex")
                    return skills_match
                return []
            
        except Exception as e:
            logger.error(f"Error extracting skills: {e}")
            return []
    
    def add_resume(self, resume_text):
        """Add a resume to the list of resumes"""
        if resume_text and resume_text.strip():
            self.resume_texts.append(resume_text.strip())
            logger.info(f"Added resume ({len(resume_text)} characters)")
            return True
        return False
    
    def clear_resumes(self):
        """Clear all resumes"""
        self.resume_texts = []
        logger.info("Cleared all resumes")
    
    def extract_text_from_pdf(self, pdf_path):
        """Extract text from a PDF file"""
        try:
            text = ""
            with fitz.open(pdf_path) as doc:
                for page in doc:
                    text += page.get_text()
            logger.info(f"Extracted text from PDF: {pdf_path}")
            return text
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
            return ""
    
    def extract_company_name(self, job_description=None):
        """Extract company name from job description"""
        if job_description is None:
            job_description = self.job_description
            
        if not job_description:
            return "Unknown_Company"
            
        try:
            # Try to extract company name using AI
            prompt = f"""
            Extract the company name from the following job description. 
            Return ONLY the company name, nothing else.
            If you cannot determine the company name, return "Unknown_Company".
            
            Job Description:
            {job_description[:2000]}  # Limit to first 2000 chars for token efficiency
            """
            
            system_message = "You extract company names from job descriptions. Respond with only the company name, nothing else."
            
            company_name = self.generate_ai_content(prompt, system_message, max_tokens=50).strip()
            
            # Clean up the company name for use in filenames
            company_name = re.sub(r'[^\w\s-]', '', company_name)  # Remove special chars
            company_name = re.sub(r'\s+', '_', company_name)      # Replace spaces with underscores
            company_name = re.sub(r'_+', '_', company_name)       # Replace multiple underscores with single
            
            if not company_name or company_name.lower() == "unknown" or company_name.lower() == "unknown_company":
                return "Unknown_Company"
                
            return company_name
            
        except Exception as e:
            logger.error(f"Error extracting company name: {e}")
            return "Unknown_Company"
    
    def extract_style_attributes(self, pdf_path=None):
        """Extract style attributes from a PDF file"""
        if pdf_path and os.path.exists(pdf_path):
            reference_pdfs = [pdf_path]
        else:
            # Look for PDFs in the uploads folder
            reference_pdfs = [os.path.join(app.config['UPLOAD_FOLDER'], f) 
                             for f in os.listdir(app.config['UPLOAD_FOLDER']) 
                             if f.endswith('.pdf')]
        
        if not reference_pdfs:
            logger.warning("No PDF files found for style reference.")
            return {}
        
        # Use the first PDF file as reference
        reference_pdf = reference_pdfs[0]
        style = {}
        
        try:
            doc = fitz.open(reference_pdf)
            
            # Extract fonts
            fonts = set()
            for page in doc:
                blocks = page.get_text("dict")["blocks"]
                for b in blocks:
                    if "lines" in b:
                        for l in b["lines"]:
                            for s in l["spans"]:
                                fonts.add(s["font"])
            
            style["fonts"] = list(fonts)
            
            # Extract page dimensions
            first_page = doc[0]
            style["width"] = first_page.rect.width
            style["height"] = first_page.rect.height
            
            # Extract margins (approximate)
            blocks = first_page.get_text("dict")["blocks"]
            if blocks:
                left_margins = [b["bbox"][0] for b in blocks]
                right_margins = [first_page.rect.width - b["bbox"][2] for b in blocks]
                top_margins = [b["bbox"][1] for b in blocks]
                bottom_margins = [first_page.rect.height - b["bbox"][3] for b in blocks]
                
                style["margins"] = {
                    "left": min(left_margins) if left_margins else 72,  # Default 1 inch
                    "right": min(right_margins) if right_margins else 72,
                    "top": min(top_margins) if top_margins else 72,
                    "bottom": min(bottom_margins) if bottom_margins else 72
                }
            
            doc.close()
            logger.info(f"Extracted style attributes from: {reference_pdf}")
            return style
            
        except Exception as e:
            logger.error(f"Error extracting style attributes: {e}")
            return {}
    
    def generate_ai_content(self, prompt, system_message="You are a helpful assistant.", max_tokens=4000):
        """Generate content using OpenAI API"""
        try:
            client = openai.OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.7
            )
            
            content = response.choices[0].message.content
            
            # Remove any markdown code block formatting that might be present
            content = re.sub(r'```html\s*', '', content)
            content = re.sub(r'```\s*$', '', content)
            
            return content
        except Exception as e:
            logger.error(f"Error generating content: {e}")
            return f"Error generating content: {str(e)}"
    
    def generate_resume(self, resume_index=0):
        """Generate a tailored resume based on the job description"""
        if not self.job_description or not self.resume_texts:
            return "Error: Job description and resume are required."
        
        resume_text = self.resume_texts[resume_index]
        today_date = datetime.now().strftime("%B %d, %Y")
        
        # Get user profile if available
        user_profile = None
        if hasattr(self, 'current_profile') and self.current_profile:
            user_profile = self.current_profile
            
        # Include skills from user profile if available
        skills_text = ""
        if user_profile and user_profile.skills:
            skills_text = "Skills to emphasize (if relevant to the job):\n" + "\n".join([f"- {skill}" for skill in user_profile.skills])
        
        prompt = f"""
        Create a tailored resume based on the following original resume and job description.
        Today's date is {today_date}.
        
        ORIGINAL RESUME:
        {resume_text}
        
        JOB DESCRIPTION:
        {self.job_description}
        
        {skills_text}
        
        INSTRUCTIONS:
        1. Create a complete, tailored resume that highlights relevant skills and experiences for this job description.
        2. The resume should be professional, clean, and well-organized.
        3. Maintain the original resume's core information but emphasize relevant skills and experiences.
        4. The resume should fit on 1-2 pages maximum.
        5. Include the following sections: Contact Information, Summary/Objective, Experience, Education, Skills, and any other relevant sections from the original resume.
        6. Format the output as complete, valid HTML with embedded CSS styling.
        7. Use a clean, professional design with appropriate spacing and formatting.
        
        The HTML should include the following CSS styling:
        - Font: Arial, Helvetica, or sans-serif
        - Base font size: 12px
        - Line height: 1.5
        - Color scheme: Professional (dark text on white background with subtle accent colors)
        - Margins: 1 inch (or equivalent in pixels)
        - Clear section headings
        - Print-friendly layout
        
        For print media, ensure:
        - No background colors that would waste ink
        - Proper page breaks
        - Appropriate margins for printing
        
        Return ONLY the complete HTML code with no markdown formatting or explanatory text.
        """
        
        system_message = "You are a professional resume writer. Always respond with complete, valid HTML only. Do not include markdown formatting, code blocks, or any explanatory text before or after the HTML. Your entire response should be valid HTML that can be directly rendered in a browser."
        
        return self.generate_ai_content(prompt, system_message)
    
    def generate_cover_letter(self, resume_index=0):
        """Generate a cover letter based on the resume and job description"""
        if not self.job_description or not self.resume_texts:
            return "Error: Job description and resume are required."
        
        resume_text = self.resume_texts[resume_index]
        today_date = datetime.now().strftime("%B %d, %Y")
        
        # Get user profile if available
        user_profile = None
        if hasattr(self, 'current_profile') and self.current_profile:
            user_profile = self.current_profile
            
        # Include name from user profile if available
        name_text = ""
        if user_profile:
            name_text = f"Applicant Name: {user_profile.first_name} {user_profile.last_name}"
            
        # Include skills from user profile if available
        skills_text = ""
        if user_profile and user_profile.skills:
            skills_text = "Skills to emphasize (if relevant to the job):\n" + "\n".join([f"- {skill}" for skill in user_profile.skills])
        
        prompt = f"""
        Create a professional cover letter based on the following resume and job description.
        Today's date is {today_date}.
        
        {name_text}
        
        RESUME:
        {resume_text}
        
        JOB DESCRIPTION:
        {self.job_description}
        
        {skills_text}
        
        INSTRUCTIONS:
        1. The cover letter must fit on a single page.
        2. Use a clean, professional layout.
        3. Include today's date ({today_date}) at the top.
        4. Follow proper business letter format including date, recipient, salutation, body, and closing.
        5. The content should be concise (3-4 paragraphs) and highlight the most relevant skills and experiences.
        6. Output complete HTML with CSS styling for a professional appearance.
        7. The letter should be personalized to the company and position.
        
        The HTML should include the following CSS styling:
        - Font: Arial, Helvetica, or sans-serif
        - Base font size: 12px
        - Line height: 1.5
        - Color: Black text on white background
        - Margins: 1 inch (or equivalent in pixels)
        - Classes for different sections (header, date, recipient, salutation, paragraphs, closing, signature)
        
        For print media, ensure:
        - No background colors
        - Proper page breaks
        - Appropriate margins for printing
        
        Return ONLY the complete HTML code with no markdown formatting or explanatory text.
        """
        
        system_message = "You are a professional cover letter writer. Always respond with complete, valid HTML only. Do not include markdown formatting, code blocks, or any explanatory text before or after the HTML. Your entire response should be valid HTML that can be directly rendered in a browser."
        
        return self.generate_ai_content(prompt, system_message)
    
    def create_application_folder(self):
        """Create a folder for the job application"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_folder_name = self.company_name.replace(" ", "_")
        folder_name = os.path.join(OUTPUT_FOLDER, f"{base_folder_name}_{timestamp}")
        
        # Ensure the folder name is unique
        os.makedirs(folder_name, exist_ok=True)
        logger.info(f"Created application folder: {folder_name}")
        return folder_name
    
    def convert_html_to_pdf(self, html_path, pdf_path):
        """Convert HTML file to PDF using browser printing
        
        This is a placeholder method - the actual PDF conversion happens in the browser
        with the print button. This method simply logs the action and returns the paths.
        """
        logger.info(f"HTML file ready for printing: {html_path}")
        logger.info(f"Recommended PDF path: {pdf_path}")
        return html_path, pdf_path
    
    def process_job_application(self):
        """Process job application by generating tailored resumes and cover letters"""
        if not self.job_description or not self.resume_texts:
            return {"error": "Job description and at least one resume are required."}
        
        try:
            # Extract company name from job description
            company_name = self.extract_company_name()
            self.company_name = company_name
            
            # Create application folder
            folder_path = self.create_application_folder()
            
            results = []
            
            # Add CSS for print buttons
            print_button_css = """
            .no-print {
                display: block;
            }
            .print-button {
                background-color: #4CAF50;
                border: none;
                color: white;
                padding: 10px 20px;
                text-align: center;
                text-decoration: none;
                display: inline-block;
                font-size: 16px;
                margin: 10px 2px;
                cursor: pointer;
                border-radius: 4px;
            }
            .print-instructions {
                margin-bottom: 10px;
                font-style: italic;
                color: #555;
            }
            @media print {
                .no-print {
                    display: none;
                }
            }
            """
            
            # Print button HTML
            print_button_html = """
            <div class="no-print" style="text-align: center; margin: 20px 0;">
                <div class="print-instructions">Click the button below to print or save as PDF</div>
                <button class="print-button" onclick="window.print()">Print / Save as PDF</button>
            </div>
            """
            
            # Process each resume
            for i, resume_text in enumerate(self.resume_texts):
                # Generate tailored resume
                resume_html = self.generate_resume(i)
                
                # Add print button to resume HTML
                if "<body>" in resume_html:
                    resume_html = resume_html.replace("<body>", f"<body>\n{print_button_html}")
                elif "<style>" in resume_html:
                    resume_html = resume_html.replace("</style>", f"</style>\n{print_button_html}")
                else:
                    resume_html = f"{print_button_html}\n{resume_html}"
                
                # Add print button CSS to resume HTML
                if "<style>" in resume_html:
                    resume_html = resume_html.replace("</style>", f"{print_button_css}\n</style>")
                else:
                    resume_html = f"<style>{print_button_css}</style>\n{resume_html}"
                
                # Save resume HTML
                resume_html_filename = f"Resume_{i+1}_{self.company_name}.html"
                resume_html_path = os.path.join(folder_path, resume_html_filename)
                with open(resume_html_path, "w", encoding="utf-8") as f:
                    f.write(resume_html)
                
                # Generate cover letter
                cover_letter_html = self.generate_cover_letter(i)
                
                # Add print button to cover letter HTML
                if "<body>" in cover_letter_html:
                    cover_letter_html = cover_letter_html.replace("<body>", f"<body>\n{print_button_html}")
                elif "<style>" in cover_letter_html:
                    cover_letter_html = cover_letter_html.replace("</style>", f"</style>\n{print_button_html}")
                else:
                    cover_letter_html = f"{print_button_html}\n{cover_letter_html}"
                
                # Add print button CSS to cover letter HTML
                if "<style>" in cover_letter_html:
                    cover_letter_html = cover_letter_html.replace("</style>", f"{print_button_css}\n</style>")
                else:
                    cover_letter_html = f"<style>{print_button_css}</style>\n{cover_letter_html}"
                
                # Save cover letter HTML
                cover_letter_html_filename = f"Cover_Letter_{i+1}_{self.company_name}.html"
                cover_letter_html_path = os.path.join(folder_path, cover_letter_html_filename)
                with open(cover_letter_html_path, "w", encoding="utf-8") as f:
                    f.write(cover_letter_html)
                
                # Try to generate PDFs if Chrome is available
                try:
                    resume_pdf_filename = f"Resume_{i+1}_{self.company_name}.pdf"
                    resume_pdf_path = os.path.join(folder_path, resume_pdf_filename)
                    self.convert_html_to_pdf(resume_html_path, resume_pdf_path)
                    
                    cover_letter_pdf_filename = f"Cover_Letter_{i+1}_{self.company_name}.pdf"
                    cover_letter_pdf_path = os.path.join(folder_path, cover_letter_pdf_filename)
                    self.convert_html_to_pdf(cover_letter_html_path, cover_letter_pdf_path)
                    
                    results.append({
                        "resume_html": resume_html_filename,
                        "resume_pdf": resume_pdf_filename,
                        "cover_letter_html": cover_letter_html_filename,
                        "cover_letter_pdf": cover_letter_pdf_filename
                    })
                except Exception as e:
                    logger.warning(f"PDF generation failed: {e}. Falling back to HTML only.")
                    results.append({
                        "resume_html": resume_html_filename,
                        "cover_letter_html": cover_letter_html_filename
                    })
            
            return {
                "success": True,
                "folder": folder_path,
                "company": company_name,
                "results": results
            }
            
        except Exception as e:
            logger.error(f"Error processing job application: {e}")
            return {"error": f"Error processing job application: {str(e)}"}

# Initialize the generator
generator = ResumeAndCoverLetterGenerator()

def allowed_file(filename):
    """Check if the file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/')
def index():
    """Render the main page"""
    # Get user profiles
    user_profiles = UserProfile.get_all_profiles()
    
    # Check if a user profile is selected
    current_profile = None
    if generator.current_user_profile:
        current_profile = generator.current_user_profile
    
    # If there are profiles but none selected, show profile selection page
    if user_profiles and not current_profile:
        return render_template('select_profile.html', 
                              profiles=user_profiles)
    
    # If no profiles exist, show create profile page
    if not user_profiles and not current_profile:
        return render_template('create_profile.html')
    
    # Get the number of resumes currently loaded
    num_resumes = len(generator.resume_texts)
    
    # Get the logs to display
    logs = log_capture_string.getvalue()
    
    # Get list of uploaded files
    uploaded_files = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        uploaded_files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) 
                         if os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], f))]
    
    # Get list of generated folders
    generated_folders = []
    if os.path.exists(OUTPUT_FOLDER):
        generated_folders = [f for f in os.listdir(OUTPUT_FOLDER) 
                            if os.path.isdir(os.path.join(OUTPUT_FOLDER, f))]
        
        # Sort by modification time (newest first)
        generated_folders.sort(key=lambda x: os.path.getmtime(os.path.join(OUTPUT_FOLDER, x)), reverse=True)
    
    # Get files in each generated folder
    generated_files = {}
    for folder in generated_folders:
        folder_path = os.path.join(OUTPUT_FOLDER, folder)
        files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
        generated_files[folder] = {
            'path': folder_path,
            'files': files,
            'time': datetime.fromtimestamp(os.path.getmtime(folder_path)).strftime('%Y-%m-%d %H:%M:%S')
        }
    
    return render_template('index.html', 
                          num_resumes=num_resumes,
                          job_description=generator.job_description,
                          logs=logs,
                          uploaded_files=uploaded_files,
                          generated_folders=generated_folders,
                          generated_files=generated_files,
                          current_profile=current_profile)

@app.route('/create_profile', methods=['GET', 'POST'])
def create_profile():
    """Create a new user profile"""
    if request.method == 'POST':
        # Get first name and last name from form
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        
        if not first_name or not last_name:
            flash('First name and last name are required', 'error')
            return redirect(request.url)
        
        # Check if resume file was uploaded
        if 'resume_file' not in request.files:
            flash('Resume file is required', 'error')
            return redirect(request.url)
        
        resume_file = request.files['resume_file']
        if resume_file.filename == '':
            flash('No resume file selected', 'error')
            return redirect(request.url)
        
        if not allowed_file(resume_file.filename):
            flash('Invalid file type for resume', 'error')
            return redirect(request.url)
        
        # Save and extract resume text
        resume_filename = secure_filename(resume_file.filename)
        resume_path = os.path.join(app.config['UPLOAD_FOLDER'], resume_filename)
        resume_file.save(resume_path)
        
        if resume_filename.endswith('.pdf'):
            resume_text = generator.extract_text_from_pdf(resume_path)
        else:
            with open(resume_path, 'r', encoding='utf-8') as f:
                resume_text = f.read()
        
        # Get portfolio and LinkedIn text if provided
        portfolio_text = request.form.get('portfolio_text', '')
        linkedin_text = request.form.get('linkedin_text', '')
        
        # Create profile with manually entered name
        profile = generator.extract_user_info(
            resume_text, 
            first_name=first_name,
            last_name=last_name,
            portfolio_text=portfolio_text, 
            linkedin_text=linkedin_text
        )
        
        # Save the profile
        try:
            profile.save()
            generator.set_user_profile(profile)
            flash(f'Profile created for {profile.full_name}', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            flash(f'Error creating profile: {str(e)}', 'error')
            return redirect(request.url)
    
    return render_template('create_profile.html')

@app.route('/select_profile/<folder_name>')
def select_profile(folder_name):
    """Select a user profile"""
    profile = UserProfile.load(folder_name)
    if profile:
        generator.set_user_profile(profile)
        flash(f'Selected profile: {profile.full_name}', 'success')
    else:
        flash('Profile not found', 'error')
    
    return redirect(url_for('index'))

@app.route('/manage_profiles')
def manage_profiles():
    """Manage user profiles"""
    profiles = UserProfile.get_all_profiles()
    return render_template('manage_profiles.html', profiles=profiles)

@app.route('/delete_profile/<folder_name>')
def delete_profile(folder_name):
    """Delete a user profile"""
    folder_path = os.path.join(USER_PROFILES_FOLDER, folder_name)
    if os.path.exists(folder_path):
        try:
            # Delete all files in the folder
            for file in os.listdir(folder_path):
                file_path = os.path.join(folder_path, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            
            # Delete the folder
            os.rmdir(folder_path)
            
            # If this was the current profile, clear it
            if generator.current_user_profile and generator.current_user_profile.folder_name == folder_name:
                generator.current_user_profile = None
                generator.resume_texts = []
            
            flash('Profile deleted successfully', 'success')
        except Exception as e:
            flash(f'Error deleting profile: {str(e)}', 'error')
    else:
        flash('Profile not found', 'error')
    
    return redirect(url_for('manage_profiles'))

@app.route('/clear_profile')
def clear_profile():
    """Clear the current user profile"""
    generator.current_user_profile = None
    generator.resume_texts = []
    flash('Current profile cleared', 'success')
    return redirect(url_for('index'))

@app.route('/upload_resume', methods=['POST'])
def upload_resume():
    """Handle resume file uploads"""
    if 'resume_files' not in request.files:
        flash('No file part', 'error')
        return redirect(request.url)
    
    files = request.files.getlist('resume_files')
    
    if not files or files[0].filename == '':
        flash('No selected file', 'error')
        return redirect(request.url)
    
    # Clear existing resumes if requested
    if request.form.get('clear_existing') == 'yes':
        generator.clear_resumes()
    
    uploaded_files = []
    
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Extract text from the file
            if filename.endswith('.pdf'):
                text = generator.extract_text_from_pdf(file_path)
            else:
                # For other file types, just read the content
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
            
            # Add the resume text
            if generator.add_resume(text):
                uploaded_files.append(filename)
    
    if uploaded_files:
        flash(f'Uploaded {len(uploaded_files)} resume(s): {", ".join(uploaded_files)}', 'success')
    else:
        flash('No valid files were uploaded', 'error')
    
    return redirect(url_for('index'))

@app.route('/generate_documents', methods=['POST'])
def generate_documents():
    """Generate resume and cover letter documents"""
    # Clear previous result from session
    if 'last_result' in session:
        session.pop('last_result')
    
    # First, handle the job description upload
    job_description_updated = False
    
    # Check if a file was uploaded
    if 'job_file' in request.files and request.files['job_file'].filename != '':
        file = request.files['job_file']
        if allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Extract text from the file
            if filename.endswith('.pdf'):
                text = generator.extract_text_from_pdf(file_path)
            else:
                # For other file types, just read the content
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
            
            generator.job_description = text
            job_description_updated = True
            logger.info(f'Job description loaded from file: {filename}')
    # Check if text was provided
    elif request.form.get('job_description', '').strip():
        generator.job_description = request.form.get('job_description').strip()
        job_description_updated = True
        logger.info('Job description updated from text input')
    
    # Check if we have what we need to generate documents
    if not generator.resume_texts and not generator.current_user_profile:
        flash('No resume loaded. Please select a profile or upload a resume.', 'error')
        return redirect(url_for('index'))
    
    if not generator.job_description:
        flash('No job description provided. Please upload or enter a job description.', 'error')
        return redirect(url_for('index'))
    
    try:
        # Extract company name
        generator.company_name = generator.extract_company_name()
        
        # Extract style attributes from the first resume PDF if available
        generator.style_attributes = generator.extract_style_attributes()
        
        # Process the job application
        result = generator.process_job_application()
        
        if 'error' in result:
            flash(f'Error generating documents: {result["error"]}', 'error')
        else:
            flash(f'Documents generated successfully for {result["company"]}!', 'success')
            flash('You can view the HTML versions and print them as PDF directly from your browser.', 'info')
            
            # Store the result in the session
            session['last_result'] = {
                'folder_path': result['folder'],
                'company_name': result['company'],
                'results': result['results']
            }
    except Exception as e:
        logger.error(f"Error generating documents: {str(e)}")
        flash(f'Error generating documents: {str(e)}', 'error')
    
    return redirect(url_for('index'))

@app.route('/view_html/<path:filename>')
def view_html(filename):
    """View an HTML file"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        logger.error(f"Error viewing HTML file: {str(e)}")
        flash(f'Error viewing HTML file: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/clear_resumes', methods=['POST'])
def clear_resumes():
    """Clear all loaded resumes"""
    generator.clear_resumes()
    flash('All resumes cleared', 'success')
    return redirect(url_for('index'))

@app.route('/clear_job_description', methods=['POST'])
def clear_job_description():
    """Clear the job description"""
    generator.job_description = ""
    flash('Job description cleared', 'success')
    return redirect(url_for('index'))

@app.route('/get_logs')
def get_logs():
    """Get the current logs"""
    return log_capture_string.getvalue()

@app.route('/edit_profile/<folder_name>', methods=['GET', 'POST'])
def edit_profile(folder_name):
    """Edit an existing user profile"""
    profile = UserProfile.load(folder_name)
    
    if not profile:
        flash('Profile not found', 'error')
        return redirect(url_for('manage_profiles'))
    
    if request.method == 'POST':
        # Update profile information
        profile.first_name = request.form.get('first_name', '').strip()
        profile.last_name = request.form.get('last_name', '').strip()
        
        # Check if new resume file was uploaded
        if 'resume_file' in request.files and request.files['resume_file'].filename:
            resume_file = request.files['resume_file']
            if allowed_file(resume_file.filename):
                # Save and extract resume text
                resume_filename = secure_filename(resume_file.filename)
                resume_path = os.path.join(app.config['UPLOAD_FOLDER'], resume_filename)
                resume_file.save(resume_path)
                
                if resume_filename.endswith('.pdf'):
                    resume_text = generator.extract_text_from_pdf(resume_path)
                else:
                    with open(resume_path, 'r', encoding='utf-8') as f:
                        resume_text = f.read()
                
                profile.resume_text = resume_text
        
        # Update portfolio and LinkedIn text
        profile.portfolio_text = request.form.get('portfolio_text', '')
        profile.linkedin_text = request.form.get('linkedin_text', '')
        
        # Re-extract skills if resume or other text changed
        profile.skills = generator.extract_skills(profile)
        
        # Save the updated profile
        try:
            profile.save()
            if generator.current_user_profile and generator.current_user_profile.folder_name == folder_name:
                generator.set_user_profile(profile)
            flash(f'Profile updated for {profile.full_name}', 'success')
            return redirect(url_for('manage_profiles'))
        except Exception as e:
            flash(f'Error updating profile: {str(e)}', 'error')
    
    return render_template('edit_profile.html', profile=profile)

@app.route('/download_file/<folder>/<filename>')
def download_file(folder, filename):
    """Download or view a generated file"""
    file_path = os.path.join(OUTPUT_FOLDER, folder, filename)
    if os.path.exists(file_path):
        # Check if we should view the file in browser (for HTML files)
        view = request.args.get('view', 'false').lower() == 'true'
        
        if view and filename.endswith('.html'):
            # Return HTML content to be displayed in browser
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                return content
            except Exception as e:
                logger.error(f"Error viewing HTML file: {str(e)}")
                flash(f'Error viewing HTML file: {str(e)}', 'error')
                return redirect(url_for('index'))
        else:
            # Download the file as attachment
            return send_file(file_path, as_attachment=True)
    else:
        flash('File not found', 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True) 