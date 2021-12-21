<?php

/**
 * Register all actions and filters for the plugin
 *
 * @link       avitrop
 * @since      1.0.0
 *
 * @package    Noti_woo
 * @subpackage Noti_woo/includes
 */

/**
 * Register all actions and filters for the plugin.
 *
 * Maintain a list of all hooks that are registered throughout
 * the plugin, and register them with the WordPress API. Call the
 * run function to execute the list of actions and filters.
 *
 * @package    Noti_woo
 * @subpackage Noti_woo/includes
 * @author     avitrop <Avitrop@gmail.com>
 */
class WebDuckUpdater
{

	private $plugin_name;
	private $version;

   
    public function __construct( $plugin_name, $version)
    {
        $this->plugin_name = $plugin_name;
		$this->version = $version;
        add_filter('plugins_api', [$this,'plugin_info'], 999, 3);
        add_filter('site_transient_update_plugins', [$this, 'push_update']);
        add_action('upgrader_process_complete', [$this,'after_update'], 10, 2);
        add_filter('plugin_row_meta', [$this, 'plugin_row_meta' ], 10, 2);
    }
    public function plugin_row_meta($plugin_meta, $plugin_file)
    {
        if ($this->plugin_name. "/" . $this->plugin_name . ".php" === $plugin_file) {
            $plugin_slug = $this->plugin_name;
            $plugin_name = __('Noti', $this->plugin_name);
    
            $row_meta = [
                'view-details' => sprintf(
                    '<a href="%s" class="thickbox open-plugin-details-modal" aria-label="%s" data-title="%s">%s</a>',
                    esc_url(network_admin_url('plugin-install.php?tab=plugin-information&plugin=' . $plugin_slug . '&TB_iframe=true&width=600&height=550')),
                    esc_attr(sprintf(__('More information about %s', $this->plugin_name), $plugin_name)),
                    esc_attr($plugin_name),
                    __('View details', $this->plugin_name)
                )
            ];
    
            $plugin_meta = array_merge($plugin_meta, $row_meta);
        }
    
        return $plugin_meta;
    }
    public function plugin_info($res, $action, $args)
    {
        // do nothing if this is not about getting plugin information
        if ('plugin_information' !== $action) {
            return false;
        }
            
        $plugin_slug = $this->plugin_name; // we are going to use it in many places in this function
            
        // do nothing if it is not our plugin
        if ($plugin_slug !== $args->slug) {
            return false;
        }
            

            // info.json is the file with the actual plugin information on your server
            $remote = wp_remote_get(
                'http://plugins.webduck.co.il/plugins/'.$this->plugin_name.'/'.$this->plugin_name.'.json',['timeout' => 40 ]       
            );
            
            if (! is_wp_error($remote) && isset($remote['response']['code']) && $remote['response']['code'] == 200 && ! empty($remote['body'])) {
                set_transient('misha_upgrade_' . $plugin_slug, $remote, 43200); // 12 hours cache
            }
      //  }
            
        if (! is_wp_error($remote) && isset($remote['response']['code']) && $remote['response']['code'] == 200 && ! empty($remote['body'])) {
            $remote = json_decode($remote['body']);
            $res = new stdClass();
            
            $res->name = $remote->name;
            $res->slug = $plugin_slug;
            $res->version = $remote->version;
            $res->tested = $remote->tested;
            $res->requires = $remote->requires;
            $res->author = '<a href="https://webduck.co.il">webduck</a>';
            $res->author_profile = 'https://webduck.co.il';
            $res->download_link = $remote->download_url;
            $res->trunk = $remote->download_url;
            $res->requires_php = '5.3';
            $res->last_updated = $remote->last_updated;
            $res->sections = array(
                        'description' => $remote->sections->description,
                        'installation' => $remote->sections->installation,
                        'changelog' => $remote->sections->changelog
                        // you can add your custom sections (tabs) here
                    );
            
            // in case you want the screenshots tab, use the following HTML format for its content:
            // <ol><li><a href="IMG_URL" target="_blank"><img src="IMG_URL" alt="CAPTION" /></a><p>CAPTION</p></li></ol>
            if (!empty($remote->sections->screenshots)) {
                $res->sections['screenshots'] = $remote->sections->screenshots;
            }
            
                   
            $res->banners = array(
                'low' => 'https://webduck.co.il/wp-content/uploads/2018/06/cropped-Untitled-1-1024x302.png',
                'high' => 'https://webduck.co.il/wp-content/uploads/2018/06/cropped-Untitled-1-1024x302.png'
            );
            return $res;
        }
            
        return false;
    }
    public function push_update($transient)
    {
        if (empty($transient->checked)) {
            return $transient;
        }
             
        // trying to get from cache first, to disable cache comment 10,20,21,22,24
        if (false == $remote = get_transient('misha_upgrade_'.$this->plugin_name)) {
             
                    // info.json is the file with the actual plugin information on your server
            $remote = wp_remote_get(
                'http://plugins.webduck.co.il/plugins/'.$this->plugin_name.'/'.$this->plugin_name.'.json',
                array(
                        'timeout' => 10,
                     )
            );
             
            if (!is_wp_error($remote) && isset($remote['response']['code']) && $remote['response']['code'] == 200 && !empty($remote['body'])) {
                set_transient('misha_upgrade_'.$this->plugin_name, $remote, 43200); // 12 hours cache
            }
        }
             
        if ($remote && !is_wp_error($remote) ) {
            $remote = json_decode($remote['body']);
             
            // your installed plugin version should be on the line below! You can obtain it dynamically of course
            if ($remote && version_compare($this->version, $remote->version, '<') && version_compare($remote->requires, get_bloginfo('version'), '<')) {
                $res = new stdClass();
                $res->slug = $this->plugin_name;
                $res->plugin = $this->plugin_name.'/'.$this->plugin_name.'.php'; // it could be just YOUR_PLUGIN_SLUG.php if your plugin doesn't have its own directory
                $res->new_version = $remote->version;
                $res->tested = $remote->tested;
                $res->package = $remote->download_url;
                $transient->response[$res->plugin] = $res;
                //$transient->checked[$res->plugin] = $remote->version;
            }
        }
        return $transient;
    }
    public function after_update($upgrader_object, $options)
    {
        if ($options['action'] == 'update' && $options['type'] === 'plugin') {
            // just clean the cache when new plugin version is installed
            delete_transient('misha_upgrade_'.$this->plugin_name);
        }
    }
}
