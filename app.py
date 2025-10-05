import os
import streamlit as st
from pathlib import Path
import tempfile
import shutil
from PIL import Image
import io
from mockup_generator.create_base import generate_image_for_product
from mockup_generator.prompt import (
    SAREE_PROMPT,
    KURTA_PAJAMA_PROMPT,
    KURTI_PROMPT,
    MEN_SHIRT_PROMPT,
    CORD_SET_PROMPT,
)

# Set page config
st.set_page_config(
    page_title="Mockup Generator",
    page_icon="🎨",
    layout="wide"
)

# Define mockup types and their prompts
MOCKUP_TYPES = {
    "SAREE": {
        "prompt": SAREE_PROMPT,
        "description": "Traditional Indian Saree"
    },
    "KURTA_PAJAMA": {
        "prompt": KURTA_PAJAMA_PROMPT,
        "description": "Kurta with Pajama (Men's Ethnic Wear)"
    },
    "KURTI": {
        "prompt": KURTI_PROMPT,
        "description": "Kurti (Women's Top)"
    },
    "MEN_SHIRT": {
        "prompt": MEN_SHIRT_PROMPT,
        "description": "Men's Formal/Informal Shirt"
    },
    "CORD_SET": {
        "prompt": CORD_SET_PROMPT,
        "description": "Women's Cord Set"
    }
}

def save_uploaded_files(uploaded_files, temp_dir):
    """Save multiple uploaded files to a temporary directory"""
    saved_paths = []
    for uploaded_file in uploaded_files:
        file_path = os.path.join(temp_dir, uploaded_file.name)
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        saved_paths.append(file_path)
    return saved_paths

def copy_folder_images(input_folder: Path, temp_dir: Path):
    """Copy images from input folder to temp directory"""
    copied_count = 0
    allowed_ext = {".png", ".jpg", ".jpeg", ".webp"}
    for file_path in input_folder.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in allowed_ext:
            shutil.copy2(file_path, temp_dir / file_path.name)
            copied_count += 1
    return copied_count

def main():
    st.title("🎨 AI Mockup Generator")
    st.write("Upload your garment images and generate professional mockups")
    
    # Mode selection
    mode = st.radio(
    "Input Mode",
    ["📤 Upload Files", "📁 Use Folder", "📂 Folder of Folders"],
    horizontal=True,
    help="Choose to upload files directly, use a single folder, or process multiple subfolders"
    )
    
    # Create two columns for the layout
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("Input")
        
        if mode == "📤 Upload Files":
            # Multiple file uploader
            uploaded_files = st.file_uploader(
                "Upload Garment Images",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
                help="Upload one or more images of the garment for better context"
            )
            
            # Display uploaded images
            if uploaded_files:
                st.write(f"**{len(uploaded_files)} image(s) uploaded**")
                cols = st.columns(min(3, len(uploaded_files)))
                for idx, uploaded_file in enumerate(uploaded_files[:3]):
                    with cols[idx]:
                        image = Image.open(uploaded_file)
                        st.image(image, caption=f"Image {idx+1}", use_container_width=True)
                if len(uploaded_files) > 3:
                    st.info(f"+ {len(uploaded_files) - 3} more image(s)")
        elif mode == "📁 Use Folder":
            # Folder input
            input_folder = st.text_input(
                "Input Folder Path",
                placeholder="/path/to/input/folder",
                help="Path to folder containing garment images"
            )
            if input_folder and Path(input_folder).exists():
                img_count = len([f for f in Path(input_folder).iterdir() 
                               if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}])
                st.success(f"✅ Found {img_count} image(s) in folder")
            elif input_folder:
                st.error("❌ Folder does not exist")

            output_folder = st.text_input(
                "Output Folder Path",
                placeholder="/path/to/output/folder",
                help="Path where generated mockups will be saved"
            )
            
        elif mode == "📂 Folder of Folders":
            parent_folder = st.text_input(
                "Parent Folder Path",
                placeholder="/path/to/parent/folder",
                help="Path to folder containing multiple garment subfolders"
            )
            output_folder = st.text_input(
                "Output Folder Path",
                placeholder="/path/to/output/folder",
                help="Path where all generated mockups will be saved"
            )

            process_mode = st.radio(
                "Processing Mode",
                ["Together (combine images in each subfolder)", "One by One (each image separately)"],
                help="Choose whether to process images jointly or individually within each subfolder"
            )
    
    with col2:
        # Mockup type selection
        st.subheader("Mockup Settings")
        
        # Select mockup type
        mockup_type = st.selectbox(
            "Select Garment Type",
            options=list(MOCKUP_TYPES.keys()),
            format_func=lambda x: f"{x} - {MOCKUP_TYPES[x]['description']}",
            help="Select the type of garment to generate mockup for"
        )
        
        # Output settings
        if mode == "📤 Upload Files":
            output_name = st.text_input(
                "Output Filename (without extension)",
                value="generated_mockup"
            )
            output_folder = None

    default_prompt = MOCKUP_TYPES[mockup_type]["prompt"]
    edited_prompt = st.text_area(
        "📝 Edit or Customize the Prompt",
        value=default_prompt,
        height=300,
        help="Modify the prompt before generating. This version will be used."
    )

    # Generate button
    if st.button("✨ Generate Mockup", type="primary", use_container_width=True):
        # Validation
        if mode == "📤 Upload Files":
            if not uploaded_files:
                st.error("Please upload at least one image!")
                return
        elif mode == "📂 Use Folder":
            if not input_folder or not Path(input_folder).exists():
                st.error("Please provide a valid input folder path!")
                return
            if not output_folder:
                st.error("Please provide an output folder path!")
                return
        elif mode == "📂 Folder of Folders":
            if not parent_folder or not Path(parent_folder).exists():
                st.error("Please provide a valid parent folder path!")
                return
            if not output_folder:
                st.error("Please provide an output folder path!")
                return
        
        with st.spinner("Generating your mockup... This may take a minute."):
            try:
                # Get the prompt for selected mockup type
                prompt = edited_prompt
                
                if mode == "📤 Upload Files":
                    # Upload mode: use temporary directories
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_dir = Path(temp_dir)
                        input_dir = temp_dir / "input"
                        output_dir = temp_dir / "output"
                        input_dir.mkdir(exist_ok=True)
                        output_dir.mkdir(exist_ok=True)
                        
                        # Save uploaded files
                        save_uploaded_files(uploaded_files, input_dir)
                        st.info(f"Processing {len(uploaded_files)} image(s)...")
                        
                        # Generate the mockup
                        generate_image_for_product(
                            product_dir=input_dir,
                            prompt=prompt,
                            out_dir=output_dir
                        )
                        
                        # Find the generated image
                        output_files = list(output_dir.glob("*.png"))
                        if output_files:
                            # Display the result
                            st.success("✅ Mockup generated successfully!")
                            result_image = Image.open(output_files[0])
                            st.image(result_image, caption="Generated Mockup", use_container_width=True)
                            
                            # Add download button
                            img_byte_arr = io.BytesIO()
                            result_image.save(img_byte_arr, format='PNG')
                            st.download_button(
                                label="⬇️ Download Mockup",
                                data=img_byte_arr.getvalue(),
                                file_name=f"{output_name}.png",
                                mime="image/png",
                                use_container_width=True
                            )
                        else:
                            st.error("Failed to generate mockup. Please try again.")
                elif mode == "📂 Use Folder":
                    # Folder mode: use specified folders
                    input_path = Path(input_folder)
                    output_path = Path(output_folder)
                    output_path.mkdir(parents=True, exist_ok=True)
                    
                    img_count = len([f for f in input_path.iterdir() 
                                   if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}])
                    st.info(f"Processing {img_count} image(s) from folder...")
                    
                    # Generate the mockup
                    generate_image_for_product(
                        product_dir=input_path,
                        prompt=prompt,
                        out_dir=output_path
                    )
                    
                    # Find the generated image
                    output_files = list(output_path.glob("*.png"))
                    if output_files:
                        st.success(f"✅ Mockup generated successfully! Saved to: {output_path}")
                        
                        # Display the result
                        result_image = Image.open(output_files[-1])  # Show the latest generated image
                        st.image(result_image, caption="Generated Mockup", use_container_width=True)
                        st.info(f"Generated {len(output_files)} mockup(s) in total")
                    else:
                        st.error("Failed to generate mockup. Please try again.")
                        
                elif mode == "📂 Folder of Folders":

                    parent_path = Path(parent_folder)
                    output_path = Path(output_folder)
                    output_path.mkdir(parents=True, exist_ok=True)

                    subfolders = [p for p in parent_path.iterdir() if p.is_dir()]
                    st.info(f"Found {len(subfolders)} subfolders to process")

                    for sub in subfolders:
                        sub_output = output_path
                        sub_output.mkdir(parents=True, exist_ok=True)

                        generate_image_for_product(
                            product_dir=sub,
                            prompt=edited_prompt,
                            out_dir=sub_output,
                            process_image_sep=(process_mode == "One by One (each image separately)")
                        )

                    st.success(f"✅ Finished processing {len(subfolders)} subfolders. Results saved in {output_path}")
            except Exception as e:
                st.error(f"An error occurred: {str(e)}")
                st.exception(e)  # This will show the full traceback for debugging

if __name__ == "__main__":
    main()