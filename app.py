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

# Get logging configuration from environment variables
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
AI_LOG_LEVEL = os.environ.get("AI_LOG_LEVEL", "INFO").upper()
AI_LOG_FULL_TEXT = os.environ.get("AI_LOG_FULL_TEXT", "true").lower() == "true"

# Convert string log levels to logging constants
LOG_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}

APP_LOG_LEVEL = LOG_LEVEL_MAP.get(LOG_LEVEL, logging.INFO)
AI_LOG_LEVEL_INT = LOG_LEVEL_MAP.get(AI_LOG_LEVEL, logging.INFO)

# Configure logging
logging.basicConfig(
    level=APP_LOG_LEVEL,
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
log_handler.setLevel(APP_LOG_LEVEL)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(log_handler)

# Create a dedicated logger for AI interactions
ai_logger = logging.getLogger("ai_interactions")
ai_logger.setLevel(AI_LOG_LEVEL_INT)
# Create a separate file handler for AI interactions
ai_log_handler = logging.FileHandler("ai_interactions.log")
ai_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
ai_logger.addHandler(ai_log_handler)
# Add to string buffer for web display
ai_logger.addHandler(log_handler)

logger.info(f"Application logging level: {LOG_LEVEL}")
logger.info(f"AI interactions logging level: {AI_LOG_LEVEL}")
logger.info(f"Full text logging enabled: {AI_LOG_FULL_TEXT}")

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
            
            # Log profile data based on settings
            if AI_LOG_FULL_TEXT:
                ai_logger.info(f"EXTRACTING SKILLS - Profile data: {combined_text}")
            else:
                combined_text_short = combined_text[:300] + "..." if len(combined_text) > 300 else combined_text
                ai_logger.info(f"EXTRACTING SKILLS - Profile data (truncated): {combined_text_short}")
            
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
            
            # Log response based on settings
            if AI_LOG_FULL_TEXT:
                ai_logger.info(f"SKILLS RESPONSE - Full: {response_text}")
            else:
                response_text_short = response_text[:300] + "..." if len(response_text) > 300 else response_text
                ai_logger.info(f"SKILLS RESPONSE - Truncated: {response_text_short}")
            
            try:
                result = json.loads(response_text)
                skills = result.get("skills", [])
                logger.info(f"Extracted {len(skills)} skills from user profile")
                # Skills list is typically small, so we always log the full list
                ai_logger.info(f"SKILLS EXTRACTED - Count: {len(skills)} - All skills: {skills}")
                return skills
            except json.JSONDecodeError:
                # Fallback: try to extract skills using regex if JSON parsing fails
                logger.warning("Failed to parse JSON response, attempting to extract skills with regex")
                
                if AI_LOG_FULL_TEXT:
                    ai_logger.warning(f"SKILLS EXTRACTION FAILED - Invalid JSON response: {response_text}")
                else:
                    response_text_short = response_text[:300] + "..." if len(response_text) > 300 else response_text
                    ai_logger.warning(f"SKILLS EXTRACTION FAILED - Invalid JSON response (truncated): {response_text_short}")
                
                import re
                # Look for anything that might be a skill (words or phrases in quotes)
                skills_match = re.findall(r'"([^"]+)"', response_text)
                if skills_match:
                    logger.info(f"Extracted {len(skills_match)} skills using regex")
                    ai_logger.info(f"SKILLS EXTRACTED (regex) - Count: {len(skills_match)} - All skills: {skills_match}")
                    return skills_match
                return []
            
        except Exception as e:
            logger.error(f"Error extracting skills: {e}")
            ai_logger.error(f"SKILLS EXTRACTION ERROR: {e}")
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
            # Log job description based on settings
            if AI_LOG_FULL_TEXT:
                ai_logger.info(f"EXTRACTING COMPANY NAME - Job description: {job_description}")
            else:
                job_desc_short = job_description[:300] + "..." if len(job_description) > 300 else job_description
                ai_logger.info(f"EXTRACTING COMPANY NAME - Job description (truncated): {job_desc_short}")
            
            prompt = f"""
            Extract the company name from the following job description. 
            Return ONLY the company name, nothing else.
            If you cannot determine the company name, return "Unknown_Company".
            
            Job Description:
            {job_description[:2000]}  # Limit to first 2000 chars for token efficiency
            """
            
            system_message = "You extract company names from job descriptions. Respond with only the company name, nothing else."
            
            company_name = self.generate_ai_content(prompt, system_message, max_tokens=50).strip()
            
            ai_logger.info(f"COMPANY NAME EXTRACTED: {company_name}")
            
            # Clean up the company name for use in filenames
            company_name = re.sub(r'[^\w\s-]', '', company_name)  # Remove special chars
            company_name = re.sub(r'\s+', '_', company_name)      # Replace spaces with underscores
            company_name = re.sub(r'_+', '_', company_name)       # Replace multiple underscores with single
            
            if not company_name or company_name.lower() == "unknown" or company_name.lower() == "unknown_company":
                return "Unknown_Company"
                
            return company_name
            
        except Exception as e:
            logger.error(f"Error extracting company name: {e}")
            ai_logger.error(f"COMPANY NAME EXTRACTION ERROR: {e}")
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
            # Log the prompt with truncation based on settings
            ai_logger.info(f"SENDING TO AI - System: {system_message}")
            
            if AI_LOG_FULL_TEXT:
                ai_logger.info(f"SENDING TO AI - Prompt: {prompt}")
            else:
                prompt_for_log = prompt[:500] + "..." if len(prompt) > 500 else prompt
                ai_logger.info(f"SENDING TO AI - Prompt (truncated): {prompt_for_log}")
            
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
            
            # Log the response with truncation based on settings
            if AI_LOG_FULL_TEXT:
                ai_logger.info(f"RECEIVED FROM AI: {content}")
            else:
                response_for_log = content[:500] + "..." if len(content) > 500 else content
                ai_logger.info(f"RECEIVED FROM AI (truncated): {response_for_log}")
            
            # Remove any markdown code block formatting that might be present
            content = re.sub(r'```html\s*', '', content)
            content = re.sub(r'```\s*$', '', content)
            
            return content
        except Exception as e:
            error_msg = f"Error generating content: {e}"
            ai_logger.error(error_msg)
            logger.error(error_msg)
            return f"Error generating content: {str(e)}"
    
    def generate_resume_content(self):
        """Generate a tailored resume based on user profile and job description"""
        logger.info("Generating tailored resume content")
        
        job_description = self.job_description[:3500]  # Limit job description length
        
        system_message = """
        You are an expert resume writer specializing in creating tailored ATS-friendly resumes.
        Focus on reorganizing and rephrasing the candidate's original resume to match the job requirements.
        Highlight relevant skills and experiences, use industry keywords from the job description, 
        and quantify achievements where possible. Keep the content professional and concise.
        
        Return a complete HTML document with embedded CSS styling that creates a clean, professional resume.
        Ensure the HTML includes proper styling for printing.
        """
        
        # Use extracted skills if available
        skills_text = ", ".join(self.extract_skills(self.current_user_profile)) if self.extract_skills(self.current_user_profile) else "Not available"
        
        # Truncate profile data for prompt (to avoid token limits)
        resume_text = self.current_user_profile.resume_text[:2000] if self.current_user_profile.resume_text else "Not provided"
        portfolio_text = self.current_user_profile.portfolio_text[:500] if self.current_user_profile.portfolio_text else "Not provided"
        linkedin_text = self.current_user_profile.linkedin_text[:500] if self.current_user_profile.linkedin_text else "Not provided"
        
        prompt = f"""
        Generate tailored resume content for the following job description, based on the candidate's profile.
        
        JOB DESCRIPTION:
        {job_description}
        
        CANDIDATE PROFILE:
        Resume: {resume_text}
        Portfolio: {portfolio_text}
        LinkedIn: {linkedin_text}
        
        Extracted Skills: {skills_text}
        
        Create a targeted resume that reorganizes and enhances the original resume content to match the job requirements.
        The resume should include:
        
        1. A brief professional summary emphasizing relevant experience
        2. Skills section with relevant technical and soft skills
        3. Work experience section (keep the original companies and dates, but tailor descriptions)
        4. Education section
        5. Any other relevant sections from the original resume
        
        Format the resume as a complete HTML document with embedded CSS for a professional appearance.
        
        CSS styling should include:
        - Clean, professional font (Arial, Helvetica, or similar sans-serif)
        - Appropriate section headings (using h2 or h3 tags)
        - Good spacing and margins
        - Consistent formatting
        - Print-friendly design (no background colors that waste ink)
        - Maximum width of 800px with centered content

        The HTML should be complete with <!DOCTYPE html>, <html>, <head>, and <body> tags.
        Include media queries for print to ensure the resume prints correctly.
        
        Keep education and work history in reverse chronological order as in the original resume.
        Do not fabricate experience or qualifications not mentioned in the original resume.
        """
        
        # Log input data
        if AI_LOG_FULL_TEXT:
            ai_logger.info(f"RESUME GENERATION - Job Description: {job_description}")
            ai_logger.info(f"RESUME GENERATION - Resume: {resume_text}")
            ai_logger.info(f"RESUME GENERATION - Portfolio: {portfolio_text}")
            ai_logger.info(f"RESUME GENERATION - LinkedIn: {linkedin_text}")
            ai_logger.info(f"RESUME GENERATION - Skills: {skills_text}")
        else:
            ai_logger.info(f"RESUME GENERATION - Job Description (truncated): {job_description[:300]}...")
            ai_logger.info(f"RESUME GENERATION - Resume (truncated): {resume_text[:300]}...")
            ai_logger.info(f"RESUME GENERATION - Portfolio (truncated): {portfolio_text[:300]}...")
            ai_logger.info(f"RESUME GENERATION - LinkedIn (truncated): {linkedin_text[:300]}...")
            ai_logger.info(f"RESUME GENERATION - Skills: {skills_text[:300]}...")
        
        try:
            resume_content = self.generate_ai_content(prompt, system_message, max_tokens=1500)
            # If the response doesn't include HTML, wrap it in basic HTML
            if "<html" not in resume_content.lower() and "<body" not in resume_content.lower():
                resume_content = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Tailored Resume</title>
                    <style>
                        body {{ 
                            font-family: Arial, Helvetica, sans-serif; 
                            line-height: 1.6; 
                            margin: 1em auto; 
                            max-width: 800px;
                            padding: 20px;
                        }}
                        h1, h2, h3 {{ 
                            color: #2c3e50; 
                            margin-top: 20px;
                        }}
                        h1 {{ 
                            text-align: center; 
                            font-size: 24px; 
                            margin-bottom: 10px; 
                        }}
                        h2 {{ 
                            font-size: 18px;
                            border-bottom: 1px solid #eee; 
                            padding-bottom: 5px; 
                            margin-top: 20px;
                        }}
                        h3 {{ font-size: 16px; }}
                        p {{ margin: 8px 0; }}
                        ul {{ margin: 8px 0; padding-left: 25px; }}
                        .section {{ margin-bottom: 20px; }}
                        .job-title {{ 
                            font-weight: bold; 
                            margin-bottom: 5px; 
                        }}
                        .job-company {{ 
                            font-weight: bold; 
                            margin-bottom: 5px; 
                        }}
                        .job-dates {{ 
                            font-style: italic; 
                            color: #666; 
                            margin-bottom: 5px; 
                        }}
                        @media print {{
                            body {{ 
                                margin: 0; 
                                padding: 0.5in; 
                                font-size: 12pt; 
                            }}
                            a {{ text-decoration: none; color: #000; }}
                        }}
                    </style>
                </head>
                <body>
                {resume_content}
                </body>
                </html>
                """
            logger.info("Resume content generated successfully")
            return resume_content
        except Exception as e:
            logger.error(f"Error generating resume content: {e}")
            ai_logger.error(f"RESUME GENERATION ERROR: {e}")
            return "Error generating resume. Please try again."
    
    def generate_cover_letter(self):
        """Generate a cover letter based on user profile and job description"""
        logger.info("Generating cover letter")
        
        job_description = self.job_description[:3500]  # Limit job description length
        
        system_message = """
        You are an expert career coach specializing in creating personalized cover letters.
        Focus on matching the candidate's experience with the job requirements.
        Be professional, engaging, and highlight relevant skills and experiences.
        The cover letter should be well-structured with an introduction, body paragraphs, and conclusion.
        Keep the tone professional but personable.
        
        Return a complete HTML document with embedded CSS styling that creates a clean, professional cover letter.
        Ensure the HTML includes proper styling for printing.
        """
        
        # Truncate profile data for prompt (to avoid token limits)
        resume_text = self.current_user_profile.resume_text[:1500] if self.current_user_profile.resume_text else "Not provided"
        portfolio_text = self.current_user_profile.portfolio_text[:500] if self.current_user_profile.portfolio_text else "Not provided"
        linkedin_text = self.current_user_profile.linkedin_text[:500] if self.current_user_profile.linkedin_text else "Not provided"
        
        prompt = f"""
        Generate a professional cover letter for the following job description, based on the candidate's profile.
        
        JOB DESCRIPTION:
        {job_description}
        
        CANDIDATE PROFILE:
        Name: {self.current_user_profile.full_name}
        Email: {self.current_user_profile.email if hasattr(self.current_user_profile, 'email') else 'example@email.com'}
        Phone: {self.current_user_profile.phone if hasattr(self.current_user_profile, 'phone') else '(123) 456-7890'}
        Address: {self.current_user_profile.address if hasattr(self.current_user_profile, 'address') else '123 Main St, City, State 12345'}
        Resume: {resume_text}
        Portfolio: {portfolio_text}
        LinkedIn: {linkedin_text}
        
        Generate a complete cover letter that is ready to be sent. Focus on matching specific experiences and skills 
        from the candidate's profile to the job requirements. Be specific and provide concrete examples from the
        candidate's background that demonstrate their suitability for the role.
        
        The cover letter should be properly formatted with:
        1. The candidate's contact information at the top (name, address, phone, email)
        2. Today's date ({datetime.now().strftime("%B %d, %Y")})
        3. Recipient's company name and "Hiring Manager" as placeholder
        4. Appropriate greeting
        5. 3-4 paragraphs of content
        6. Professional closing
        7. Candidate's name
        
        Format the letter as a complete HTML document with embedded CSS for a professional appearance.
        
        CSS styling should include:
        - Clean, professional font (Arial, Helvetica, or similar sans-serif)
        - Appropriate spacing and margins
        - Consistent formatting for date, greeting, body, and signature
        - Print-friendly design (no background colors that waste ink)
        - Maximum width of 800px with centered content

        The HTML should be complete with <!DOCTYPE html>, <html>, <head>, and <body> tags.
        Include media queries for print to ensure the letter prints correctly.
        """
        
        # Log input data
        if AI_LOG_FULL_TEXT:
            ai_logger.info(f"COVER LETTER GENERATION - Job Description: {job_description}")
            ai_logger.info(f"COVER LETTER GENERATION - Resume: {resume_text}")
            ai_logger.info(f"COVER LETTER GENERATION - Portfolio: {portfolio_text}")
            ai_logger.info(f"COVER LETTER GENERATION - LinkedIn: {linkedin_text}")
        else:
            ai_logger.info(f"COVER LETTER GENERATION - Job Description (truncated): {job_description[:300]}...")
            ai_logger.info(f"COVER LETTER GENERATION - Resume (truncated): {resume_text[:300]}...")
            ai_logger.info(f"COVER LETTER GENERATION - Portfolio (truncated): {portfolio_text[:300]}...")
            ai_logger.info(f"COVER LETTER GENERATION - LinkedIn (truncated): {linkedin_text[:300]}...")
        
        try:
            cover_letter = self.generate_ai_content(prompt, system_message, max_tokens=1000)
            # If the response doesn't include HTML, wrap it in basic HTML
            if "<html" not in cover_letter.lower() and "<body" not in cover_letter.lower():
                cover_letter = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Cover Letter</title>
                    <style>
                        body {{ 
                            font-family: Arial, Helvetica, sans-serif; 
                            line-height: 1.6; 
                            margin: 1em auto; 
                            max-width: 800px;
                            padding: 20px;
                        }}
                        .header {{
                            margin-bottom: 30px;
                        }}
                        .contact-info {{
                            margin-bottom: 20px;
                        }}
                        .date {{ 
                            margin-bottom: 20px; 
                        }}
                        .recipient {{
                            margin-bottom: 20px;
                        }}
                        .greeting {{ 
                            margin-bottom: 20px; 
                        }}
                        .body {{ 
                            margin-bottom: 20px; 
                        }}
                        .body p {{ 
                            margin-bottom: 15px; 
                        }}
                        .closing {{ 
                            margin-bottom: 10px; 
                        }}
                        .signature {{ 
                            margin-top: 30px; 
                            font-weight: bold; 
                        }}
                        @media print {{
                            body {{ 
                                margin: 0; 
                                padding: 0.5in; 
                                font-size: 12pt; 
                            }}
                        }}
                    </style>
                </head>
                <body>
                <div class="header">
                    <div class="contact-info">
                        {self.current_user_profile.full_name}<br>
                        {self.current_user_profile.address if hasattr(self.current_user_profile, 'address') else '123 Main St'}<br>
                        {self.current_user_profile.email if hasattr(self.current_user_profile, 'email') else 'example@email.com'}<br>
                        {self.current_user_profile.phone if hasattr(self.current_user_profile, 'phone') else '(123) 456-7890'}
                    </div>
                    <div class="date">{datetime.now().strftime("%B %d, %Y")}</div>
                    <div class="recipient">
                        {self.company_name}<br>
                        Hiring Manager
                    </div>
                </div>
                <div class="greeting">Dear Hiring Manager,</div>
                <div class="body">
                {cover_letter}
                </div>
                <div class="closing">Sincerely,</div>
                <div class="signature">{self.current_user_profile.full_name}</div>
                </body>
                </html>
                """
            logger.info("Cover letter generated successfully")
            return cover_letter
        except Exception as e:
            logger.error(f"Error generating cover letter: {e}")
            ai_logger.error(f"COVER LETTER GENERATION ERROR: {e}")
            return "Error generating cover letter. Please try again."
    
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
            logger.info("Extracting company name from job description")
            company_name = self.extract_company_name()
            self.company_name = company_name
            
            # Create application folder
            folder_path = self.create_application_folder()
            logger.info(f"Created application folder: {folder_path}")
            
            # Analyze job requirements
            logger.info("Analyzing job requirements")
            
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
                logger.info("Extracting skills from user profile data")
                
                # Generate tailored resume
                logger.info("Generating tailored resume content")
                resume_html = self.generate_resume_content()
                
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
                logger.info("Generating cover letter")
                cover_letter_html = self.generate_cover_letter()
                
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
            
            logger.info("Document generation complete!")
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

@app.route('/ai_logs')
def ai_logs():
    """View AI interaction logs"""
    try:
        # First try with utf-8 encoding and error handling
        try:
            with open('ai_interactions.log', 'r', encoding='utf-8', errors='replace') as f:
                logs = f.readlines()
            logger.info("AI logs loaded using utf-8 encoding with error replacement")
        except Exception as e:
            # If that fails, try with latin-1 encoding which can handle all byte values
            logger.warning(f"Error loading AI logs with utf-8: {str(e)}")
            with open('ai_interactions.log', 'r', encoding='latin-1') as f:
                logs = f.readlines()
            logger.info("AI logs loaded using latin-1 encoding")
        
        # Format logs for display
        formatted_logs = []
        for log in logs:
            # Clean the log entry to ensure it's valid HTML
            log = log.replace('<', '&lt;').replace('>', '&gt;')
            
            # Apply appropriate CSS classes based on log content
            if "ERROR" in log or "FAILED" in log:
                formatted_logs.append(f"<div class='error-message'>{log}</div>")
            elif "WARNING" in log:
                formatted_logs.append(f"<div class='warning-message'>{log}</div>")
            elif "SENDING TO AI" in log or "EXTRACTING" in log:
                formatted_logs.append(f"<div class='outgoing-message'>{log}</div>")
            elif "RECEIVED FROM AI" in log or "RESPONSE" in log or "EXTRACTED" in log:
                formatted_logs.append(f"<div class='incoming-message'>{log}</div>")
            elif "<html" in log.lower() or "</html>" in log.lower():
                formatted_logs.append(f"<div class='html-content'>{log}</div>")
            else:
                formatted_logs.append(f"<div>{log}</div>")
        
        return render_template('ai_logs.html', logs=formatted_logs)
    except Exception as e:
        error_msg = f"Error loading AI logs: {str(e)}"
        logger.error(error_msg)
        flash(error_msg, 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True) 