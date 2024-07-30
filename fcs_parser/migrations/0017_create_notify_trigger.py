from django.db import migrations, connection

def create_notify_function_and_trigger(apps, schema_editor):
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE OR REPLACE FUNCTION notify_new_file() RETURNS trigger AS $$
            DECLARE
            BEGIN
                PERFORM pg_notify('new_file', NEW.id::text);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        cursor.execute("""
            CREATE TRIGGER trigger_new_file
            AFTER INSERT ON experiment_files
            FOR EACH ROW EXECUTE FUNCTION notify_new_file();
        """)

def drop_notify_function_and_trigger(apps, schema_editor):
    with connection.cursor() as cursor:
        cursor.execute("""
            DROP TRIGGER IF EXISTS trigger_new_file ON experiment_files;
        """)
        cursor.execute("""
            DROP FUNCTION IF EXISTS notify_new_file;
        """)

class Migration(migrations.Migration):

    dependencies = [
      
        ('fcs_parser', '0016_gatemodel'),
    ]

    operations = [
        migrations.RunPython(create_notify_function_and_trigger, reverse_code=drop_notify_function_and_trigger),
    ]
